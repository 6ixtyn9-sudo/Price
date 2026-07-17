"""Profit-protection logic for the paper-trading exploration layer.

This module is pure logic. It contains no network calls, broker clients,
or side effects. All conditions are configurable and default to off or
conservative values.

It evaluates three profit-exit conditions:
1. Hard R-multiple take-profit (e.g. exit when unrealized R >= 3.0).
2. End-of-day profit lock (e.g. exit equities near 16:00 ET if profit is > 0.75R).
3. Profit giveback lock (e.g. exit if trade peaked at > 2.0R and then gave back 1.0R).

NOTE on `extreme_price`: `max_unrealized_r` uses the extreme close price
tracked by `StopState` across daily scans, not the tick-level intraday high.
This makes the giveback check conservative (it understates the peak, biasing
toward NOT exiting).

NOTE on `StopState.entry_price`: `unrealized_r` is computed against the entry
price recorded when the stop was initially attached, rather than Alpaca's live
`avg_entry_price`. This ensures the R-multiple scales exactly as the
`r_per_share` distance originally intended.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import pandas_market_calendars as mcal
from price.stops import StopState


@dataclass
class ProfitPolicy:
    """Profit-protection configuration. All checks are opt-in via thresholds > 0."""

    # Hard R-multiple take-profit. Exit when unrealized_R >= take_profit_r.
    take_profit_r: Optional[float] = 3.0

    # End-of-day profit lock (equities only by default).
    # Exit when unrealized_R >= eod_profit_lock_r AND within
    # eod_lock_minutes_before_close minutes of the regular-session close.
    eod_profit_lock_r: Optional[float] = 0.75
    eod_lock_minutes_before_close: int = 45

    # Profit giveback lock.
    # Exit when max_unrealized_R >= giveback_trigger_r AND current
    # unrealized_R has fallen by >= max_giveback_r from the peak.
    giveback_trigger_r: Optional[float] = 2.0
    max_giveback_r: float = 1.0

    # Asset-class behavioural flags.
    # apply_eod_to_crypto: if False (default), EOD lock never fires on
    # symbols whose name contains '/' (Alpaca crypto naming convention).
    apply_eod_to_crypto: bool = False
    # apply_eod_to_futures: if False (default), EOD lock is suppressed for
    # FUT/* symbols (futures session handling is less mature).
    apply_eod_to_futures: bool = False


def is_crypto_symbol(symbol: str) -> bool:
    """True if symbol contains '/' (Alpaca crypto convention: BTC/USD)."""
    return "/" in symbol


def is_futures_symbol(symbol: str) -> bool:
    """True if symbol starts with 'FUT/' (current futures naming)."""
    return symbol.startswith("FUT/")


def minutes_to_ny_close(now_utc: datetime) -> Optional[float]:
    """
    Minutes from `now_utc` until the NYSE/NASDAQ regular-session close.
    Returns None on weekends, US market holidays, or when the market is
    already closed for the day.
    """
    try:
        nyse = mcal.get_calendar('NYSE')
        schedule = nyse.schedule(start_date=now_utc.date(), end_date=now_utc.date())
        if schedule.empty:
            return None
            
        close_time = schedule.iloc[0]['market_close']
        if close_time.tzinfo is None:
            close_time = close_time.tz_localize('UTC')
        else:
            close_time = close_time.tz_convert('UTC')
            
        # If market is already closed, return None
        if now_utc >= close_time:
            return None
            
        return (close_time - now_utc).total_seconds() / 60.0
    except Exception:
        return None


def check_profit_exits(
    symbol: str,
    side: str,
    current_price: float,
    state: Optional[StopState],
    policy: ProfitPolicy,
    now_utc: Optional[datetime] = None,
) -> List[dict]:
    """
    Check all three profit-protection conditions for one position.
    Returns a list of exit-reason dicts (empty list = no exit).
    Fails open (no exit) if state is missing or data is invalid.
    """
    exits = []
    
    if state is None:
        return exits
        
    if state.r_per_share is None or state.r_per_share <= 0:
        return exits
        
    try:
        # Check if float is NaN or infinite
        float_price = float(current_price)
        if float_price != float_price or float_price == float('inf') or float_price == float('-inf'):
            return exits
    except (ValueError, TypeError):
        return exits
        
    unrealized_r = state.unrealized_r_multiple(current_price)
    
    # max_unrealized_r derives from extreme_price
    max_unrealized_r = None
    if state.extreme_price is not None:
        if side == "long":
            max_unrealized_r = (state.extreme_price - state.entry_price) / state.r_per_share
        else:
            max_unrealized_r = (state.entry_price - state.extreme_price) / state.r_per_share
            
        # Ensure max_unrealized_r is at least current unrealized_r
        # (in case current_price is a new extreme but state.extreme_price hasn't updated yet)
        if max_unrealized_r < unrealized_r:
            max_unrealized_r = unrealized_r
            
    giveback_r = max_unrealized_r - unrealized_r if max_unrealized_r is not None else 0.0

    # 1. Take Profit
    if policy.take_profit_r is not None and policy.take_profit_r > 0:
        if unrealized_r >= policy.take_profit_r:
            exits.append({
                "profit_exit_type": "take_profit_r",
                "reason": f"take_profit_r_hit: unrealized {unrealized_r:.2f}R >= {policy.take_profit_r:.2f}R",
                "unrealized_r": unrealized_r,
                "max_unrealized_r": max_unrealized_r,
                "giveback_r": giveback_r,
                "minutes_to_close": None,
                "entry_price": state.entry_price,
                "r_per_share": state.r_per_share,
            })

    # 2. Profit Giveback
    if policy.giveback_trigger_r is not None and policy.giveback_trigger_r > 0:
        if max_unrealized_r is not None and max_unrealized_r >= policy.giveback_trigger_r:
            if giveback_r >= policy.max_giveback_r:
                exits.append({
                    "profit_exit_type": "profit_giveback",
                    "reason": f"profit_giveback: max {max_unrealized_r:.2f}R, now {unrealized_r:.2f}R, gave back {giveback_r:.2f}R",
                    "unrealized_r": unrealized_r,
                    "max_unrealized_r": max_unrealized_r,
                    "giveback_r": giveback_r,
                    "minutes_to_close": None,
                    "entry_price": state.entry_price,
                    "r_per_share": state.r_per_share,
                })

    # 3. End of Day Profit Lock
    if policy.eod_profit_lock_r is not None and policy.eod_profit_lock_r > 0:
        # Check asset class gates
        if is_crypto_symbol(symbol) and not policy.apply_eod_to_crypto:
            pass
        elif is_futures_symbol(symbol) and not policy.apply_eod_to_futures:
            pass
        else:
            if unrealized_r >= policy.eod_profit_lock_r:
                dt_now = now_utc if now_utc is not None else datetime.now(timezone.utc)
                mins = minutes_to_ny_close(dt_now)
                
                if mins is not None and mins <= policy.eod_lock_minutes_before_close:
                    exits.append({
                        "profit_exit_type": "eod_profit_lock",
                        "reason": f"eod_profit_lock: {unrealized_r:.2f}R profit with {mins:.0f} minutes to NY close",
                        "unrealized_r": unrealized_r,
                        "max_unrealized_r": max_unrealized_r,
                        "giveback_r": giveback_r,
                        "minutes_to_close": mins,
                        "entry_price": state.entry_price,
                        "r_per_share": state.r_per_share,
                    })

    return exits

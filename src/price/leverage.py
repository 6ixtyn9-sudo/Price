"""Leverage budgets: gross notional exposure cap + real-time margin cushion.

Steady-state / overnight-hold leverage only (see RiskLimits.target_leverage_
multiple's docstring for why: this system's exit policy holds positions
across multiple bars, so it targets Reg T's 2x overnight multiplier, not
Alpaca's 4x intraday-only rate, which must be reduced before market close).

This module is pure risk logic: no network calls, no broker client. It
answers two independent questions that the R-based aggregate-risk budget
(price.stops.check_aggregate_risk_budget) does NOT answer:

  1. Gross notional budget: leverage changes how much NOTIONAL a given
     amount of equity can control, not how much R a given stop distance
     risks. A low-ATR%%, high-priced name can carry a small R (tight
     stop) while still deploying huge notional/margin exposure -- exactly
     the case leverage amplifies. check_gross_notional_budget bounds
     total deployed notional (existing positions + a proposed new one)
     to equity * target_leverage_multiple.

  2. Margin cushion: an honest backstop against our own approximate
     notional math. Rather than trust our arithmetic alone, it compares
     the broker's REAL-TIME buying_power (ground truth, reflecting
     whatever Alpaca's actual margin formula says right now) against our
     own self-imposed leverage ceiling (equity * target_leverage_
     multiple), and blocks new entries once too little of that
     self-imposed capacity remains unused. This catches drift between
     our notional tracking and reality (fees, other activity, short-side
     margin differences, etc.) that check_gross_notional_budget alone
     cannot.

Both fail OPEN (allowed=True) when the data to enforce them is missing,
consistent with every other equity-dependent lever in this project: a
gate only activates when there is real data to enforce it with.
"""

from typing import List, Optional


def total_open_notional(open_positions: List[dict]) -> float:
    """Sum of absolute notional exposure across a list of position dicts
    (as returned by trading.get_open_positions().to_dict("records")).

    Prefers the broker-reported 'market_value' (true, includes any price
    drift since entry); falls back to qty * avg_entry_price when
    market_value is absent. Skips a row entirely (contributes 0) rather
    than raising if neither is computable -- this must never crash a scan.
    """
    if not open_positions:
        return 0.0
    total = 0.0
    for pos in open_positions:
        try:
            mv = pos.get("market_value")
            if mv is not None and float(mv) == float(mv):
                total += abs(float(mv))
                continue
        except (TypeError, ValueError):
            pass
        try:
            qty = pos.get("qty")
            price = pos.get("avg_entry_price")
            if qty is not None and price is not None:
                total += abs(float(qty) * float(price))
        except (TypeError, ValueError):
            continue
    return total


def check_gross_notional_budget(
    proposed_notional: Optional[float],
    open_positions_notional: Optional[float],
    equity: Optional[float],
    target_leverage_multiple: Optional[float],
) -> tuple:
    """Would adding a new trade with `proposed_notional` dollars of
    exposure push the book's TOTAL deployed notional past
    equity * target_leverage_multiple? Returns (allowed, detail).

    Fails open when equity, the multiple, or the proposed notional is
    unknown, or when the multiple is <= 0 (explicit disable).

    open_positions_notional must be EXPLICITLY provided (not None) for
    this check to activate -- it is deliberately NOT defaulted to 0.0.
    This is what keeps the check strictly opt-in: a caller that only has
    `equity` set for an unrelated reason (e.g. sizing's volatility rail)
    must not incidentally activate a book-wide notional cap it never asked
    for. Only a caller that has actually computed and passed current open
    notional is asking this check to do real work.
    """
    if (
        equity is None
        or target_leverage_multiple is None
        or target_leverage_multiple <= 0
        or proposed_notional is None
        or open_positions_notional is None
    ):
        return True, {"reason": "gross notional budget inactive (missing equity/multiple/proposed notional/open notional)"}

    current = open_positions_notional
    budget = target_leverage_multiple * float(equity)
    projected = current + proposed_notional
    allowed = projected <= budget
    return allowed, {
        "current_open_notional": round(current, 2),
        "proposed_notional": round(proposed_notional, 2),
        "projected_open_notional": round(projected, 2),
        "budget_notional": round(budget, 2),
        "target_leverage_multiple": target_leverage_multiple,
    }


def check_margin_cushion(
    buying_power: Optional[float],
    equity: Optional[float],
    target_leverage_multiple: Optional[float],
    margin_cushion_pct: Optional[float],
) -> tuple:
    """Real-time broker-truth backstop: block a new entry once the
    fraction of our SELF-IMPOSED leverage ceiling that remains as actual
    buying power drops below `margin_cushion_pct`. Returns (allowed, detail).

    remaining_fraction = buying_power / (equity * target_leverage_multiple)
    allowed iff remaining_fraction >= margin_cushion_pct

    Deliberately normalizes against OUR OWN target_leverage_multiple, not
    whatever multiplier the broker itself might allow (which can be
    higher, e.g. Alpaca's 4x intraday rate) -- this is a self-imposed
    ceiling, not a broker-capacity check.

    Fails open when any input is missing, the multiple is <= 0, or the
    cushion is None/<=0 (explicit disable).
    """
    if (
        buying_power is None
        or equity is None
        or target_leverage_multiple is None
        or target_leverage_multiple <= 0
        or margin_cushion_pct is None
        or margin_cushion_pct <= 0
    ):
        return True, {"reason": "margin cushion check inactive (missing buying_power/equity/multiple/cushion)"}

    ceiling = target_leverage_multiple * float(equity)
    if ceiling <= 0:
        return True, {"reason": "margin cushion check inactive (non-positive leverage ceiling)"}

    remaining_fraction = float(buying_power) / ceiling
    allowed = remaining_fraction >= margin_cushion_pct
    return allowed, {
        "buying_power": round(float(buying_power), 2),
        "leverage_ceiling": round(ceiling, 2),
        "remaining_fraction": round(remaining_fraction, 4),
        "margin_cushion_pct": margin_cushion_pct,
    }

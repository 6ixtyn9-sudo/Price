"""Broker-side protective stop management: real capital protection.

Implements the "small losses, large profits" R-multiple design agreed
with the operator:

  - The INITIAL protective stop is set k_stop * ATR(14) away from the
    actual fill price at entry. That distance defines R -- the dollar
    risk committed to the trade -- BEFORE the trade is live. A REAL
    broker-side stop order enforces this continuously (Alpaca watches
    it tick-by-tick), not just when paper_trade.py happens to run.
  - Once the trade reaches +1R unrealized, the stop ratchets to
    breakeven. From that point the trade cannot lose money; the only
    outcomes left are a scratch at zero or a win of some size.
  - Beyond +1R, the stop trails the highest favorable close since entry
    by k_trail * ATR(14) (a chandelier exit) -- looser than the entry
    stop, so a real trend gets room to develop instead of being capped.
  - The stop only ever moves in the trade's favor. It is never loosened.

This module is pure risk/state logic: no network calls, no broker
client. It decides WHAT the protective stop should be and tracks each
position's R-state on disk. Actually placing/replacing/canceling the
broker-side order is trading.py's job; wiring the two together for a
live scan is paper_trade.py's job. This module does NOT decide when to
enter a trade (monitor.py) or how big it should be (sizing.py) -- it
only manages the protective stop for a position that already exists.
"""

import json
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from price.config import DATA_DIR


STOP_STATE_PATH = DATA_DIR / "stop_state.json"
STOPOUT_JOURNAL_PATH = DATA_DIR / "stopout_journal.json"

# k_stop: initial protective stop distance, in multiples of ATR(14).
# Operator-agreed default: 2.0 (balanced -- caps loss while tolerating
# ordinary daily noise). This is also the number that makes sizing.py's
# volatility rail dollar-risk math literally true (previously the rail
# assumed a 1x-ATR stop that no actual order enforced).
DEFAULT_STOP_ATR_MULT = 2.0

# k_trail: chandelier trailing-stop distance, in multiples of ATR(14),
# used only AFTER the trade has reached +1R. Operator-agreed default:
# 3.0 (looser than the entry stop, so a real trend has room to run).
DEFAULT_TRAIL_ATR_MULT = 3.0

# Unrealized R-multiple that triggers the move-to-breakeven ratchet.
BREAKEVEN_TRIGGER_R = 1.0

# Consecutive same-UTC-day stop-outs on one symbol before the whipsaw
# circuit breaker benches it for the rest of the day. Tight stops mean
# more stop-outs; this exists so "small losses" doesn't quietly become
# "many small losses in one bad, choppy day."
WHIPSAW_STOPOUT_LIMIT = 2


@dataclass
class StopState:
    """Persisted R-state for one open position's protective stop.

    All price fields are in the position's native price units. `side`
    is the POSITION's side ("long" or "short"), not the order side.
    """

    symbol: str
    side: str
    qty: float
    entry_price: float
    initial_stop_price: float
    current_stop_price: float
    r_per_share: float
    stage: str = "initial"  # "initial" | "breakeven" | "trailing"
    extreme_price: Optional[float] = None
    stop_order_id: Optional[str] = None
    opened_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def initial_r_dollars(self) -> float:
        """Dollar risk committed to this trade at entry (R)."""
        return abs(self.r_per_share) * abs(self.qty)

    def current_risk_dollars(self) -> float:
        """Dollars still genuinely at risk given the CURRENT resting stop.

        A position whose stop has ratcheted to breakeven or better
        contributes ZERO to the book's aggregate open risk -- it cannot
        lose money anymore, so it should not consume risk budget that a
        new, genuinely-at-risk trade needs. This is what lets the book
        free up room for new entries as winners de-risk themselves.
        """
        if self.side == "long":
            open_risk_per_share = max(0.0, self.entry_price - self.current_stop_price)
        else:
            open_risk_per_share = max(0.0, self.current_stop_price - self.entry_price)
        return open_risk_per_share * abs(self.qty)

    def unrealized_r_multiple(self, current_price: float) -> Optional[float]:
        """How many multiples of the initial R this trade currently shows,
        marked at `current_price`. None if R is degenerate (<= 0)."""
        if self.r_per_share is None or self.r_per_share <= 0:
            return None
        if self.side == "long":
            return (current_price - self.entry_price) / self.r_per_share
        return (self.entry_price - current_price) / self.r_per_share


def compute_initial_stop(entry_price: float, atr: float, side: str,
                          k_stop: float = DEFAULT_STOP_ATR_MULT) -> tuple:
    """Return (stop_price, r_per_share) for a fresh entry.

    r_per_share == the per-share dollar distance to the stop == the per-
    share R for this trade. Raises ValueError on a non-finite/non-positive
    entry_price or atr, or an unrecognized side -- callers must validate
    inputs before calling (this is pure math, not a safety gate).
    """
    if entry_price is None or entry_price != entry_price or entry_price <= 0:
        raise ValueError(f"invalid entry_price: {entry_price}")
    if atr is None or atr != atr or atr <= 0:
        raise ValueError(f"invalid atr: {atr}")
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")

    dist = k_stop * atr
    if side == "long":
        stop_price = entry_price - dist
    else:
        stop_price = entry_price + dist
    return stop_price, dist


def new_stop_state(symbol: str, side: str, qty: float, entry_price: float,
                    atr: float, k_stop: float = DEFAULT_STOP_ATR_MULT,
                    stop_order_id: Optional[str] = None) -> StopState:
    """Build the initial StopState for a freshly-filled position."""
    stop_price, r_per_share = compute_initial_stop(entry_price, atr, side, k_stop)
    now = datetime.now(timezone.utc).isoformat()
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
        stop_order_id=stop_order_id,
        opened_at=now,
        updated_at=now,
    )


def update_trailing_stop(state: StopState, current_price: float,
                          atr: Optional[float],
                          k_trail: float = DEFAULT_TRAIL_ATR_MULT,
                          breakeven_trigger_r: float = BREAKEVEN_TRIGGER_R) -> StopState:
    """Recompute the desired stop for `state` given the latest price/ATR.

    Pure function; returns a NEW StopState and never mutates the input.
    The stop only ever RATCHETS in the trade's favor:
      - Below +1R: unchanged (the initial stop stands).
      - At/above +1R: at least breakeven; and once ATR is available and
        the chandelier level (extreme favorable close since entry, minus
        k_trail * ATR) is better than breakeven, trail there instead.
    If `current_price` is worse than the current stop, the caller is
    expected to have already exited the trade via the broker-side stop;
    this function still returns a valid (unchanged) state defensively.

    KNOWN SHARP EDGE (documented, not a bug -- found during red-team
    review): the trail distance is recomputed from the CURRENT ATR every
    call, not the ATR the trade was entered/previously trailed under. If
    volatility crushes sharply between scans (e.g. a name goes from an
    active, wide-ranging regime to a quiet one), the chandelier distance
    can tighten a LOT in a single scan, potentially stopping the trade out
    on price action that would have been unremarkable noise under the
    wider ATR regime it was trailing under moments before. This is a
    consequence of always using LIVE ATR (deliberate: a stale ATR would be
    equally wrong in the other direction), not a defect to silently patch
    around. Operators watching a name through a volatility regime change
    should be aware the trail can re-tighten aggressively.
    """
    if state is None:
        return state
    if state.r_per_share is None or state.r_per_share <= 0:
        # Degenerate R -- cannot ratchet meaningfully; leave state as-is.
        return state

    long = state.side == "long"
    prior_extreme = state.extreme_price if state.extreme_price is not None else state.entry_price
    extreme = max(prior_extreme, current_price) if long else min(prior_extreme, current_price)

    r_mult = state.unrealized_r_multiple(current_price)
    candidate_stop = state.current_stop_price
    stage = state.stage

    if r_mult is not None and r_mult >= breakeven_trigger_r:
        breakeven_stop = state.entry_price
        candidate_stop = max(candidate_stop, breakeven_stop) if long else min(candidate_stop, breakeven_stop)
        if stage == "initial":
            stage = "breakeven"

        if atr is not None and atr == atr and atr > 0:
            if long:
                chandelier = extreme - k_trail * atr
                if chandelier > candidate_stop:
                    candidate_stop = chandelier
                    stage = "trailing"
            else:
                chandelier = extreme + k_trail * atr
                if chandelier < candidate_stop:
                    candidate_stop = chandelier
                    stage = "trailing"

    improved = (candidate_stop > state.current_stop_price) if long else (candidate_stop < state.current_stop_price)
    changed = improved or (extreme != prior_extreme) or (stage != state.stage)
    if not changed:
        return state

    return replace(
        state,
        current_stop_price=candidate_stop if improved else state.current_stop_price,
        extreme_price=extreme,
        stage=stage,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------
# Persistence (mirrors risk_limits.py's cooldown-journal pattern)
# --------------------------------------------------------------------------

def load_stop_states(path: Optional[Path] = None) -> Dict[str, StopState]:
    p = Path(path) if path else Path(STOP_STATE_PATH)
    if not p.exists():
        return {}
    try:
        with open(p, "r") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    out: Dict[str, StopState] = {}
    for sym, d in raw.items():
        try:
            out[sym.upper()] = StopState(**d)
        except TypeError:
            continue
    return out


def save_stop_states(states: Dict[str, StopState], path: Optional[Path] = None) -> None:
    p = Path(path) if path else Path(STOP_STATE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump({k: v.to_dict() for k, v in states.items()}, f, indent=2)


def remove_stop_state(symbol: str, path: Optional[Path] = None) -> bool:
    states = load_stop_states(path)
    key = symbol.upper()
    if key in states:
        del states[key]
        save_stop_states(states, path)
        return True
    return False


# --------------------------------------------------------------------------
# Aggregate open-risk budget (the leverage prerequisite)
# --------------------------------------------------------------------------

def aggregate_open_risk_dollars(states: Dict[str, StopState]) -> float:
    """Sum of `current_risk_dollars()` across every tracked open position.

    Positions whose stop has ratcheted to breakeven-or-better contribute
    zero, so the book's risk budget frees up as winners de-risk -- this
    is deliberate, not an oversight.
    """
    if not states:
        return 0.0
    return sum(s.current_risk_dollars() for s in states.values())


def check_aggregate_risk_budget(
    proposed_r_dollars: Optional[float],
    states: Dict[str, StopState],
    equity: Optional[float],
    max_aggregate_open_risk_pct: Optional[float],
) -> tuple:
    """Would adding a new trade with `proposed_r_dollars` of risk breach
    the book-wide risk budget? Returns (allowed: bool, detail: dict).

    Fails OPEN (allowed=True) when equity, the cap, or the proposed R is
    unknown -- this is a gate that only activates when there is real
    data to enforce it with, consistent with every other lever in this
    project's graceful-degradation doctrine.
    """
    if (
        equity is None
        or max_aggregate_open_risk_pct is None
        or max_aggregate_open_risk_pct <= 0
        or proposed_r_dollars is None
    ):
        return True, {"reason": "aggregate risk cap inactive (missing equity/cap/proposed R)"}

    current = aggregate_open_risk_dollars(states)
    budget = max_aggregate_open_risk_pct * float(equity)
    projected = current + proposed_r_dollars
    allowed = projected <= budget
    return allowed, {
        "current_open_risk_dollars": round(current, 2),
        "proposed_r_dollars": round(proposed_r_dollars, 2),
        "projected_open_risk_dollars": round(projected, 2),
        "budget_dollars": round(budget, 2),
        "max_aggregate_open_risk_pct": max_aggregate_open_risk_pct,
    }


# --------------------------------------------------------------------------
# Whipsaw circuit breaker
# --------------------------------------------------------------------------

def _load_stopout_journal(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else Path(STOPOUT_JOURNAL_PATH)
    if not p.exists():
        return {}
    try:
        with open(p, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_stopout_journal(state: dict, path: Optional[Path] = None) -> None:
    p = Path(path) if path else Path(STOPOUT_JOURNAL_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


def record_stopout(symbol: str, path: Optional[Path] = None) -> None:
    """Log that `symbol` was just stopped out (broker-side stop filled)."""
    journal = _load_stopout_journal(path)
    key = symbol.upper()
    journal.setdefault(key, [])
    journal[key].append(datetime.now(timezone.utc).isoformat())
    _save_stopout_journal(journal, path)


def stopout_count_today(symbol: str, path: Optional[Path] = None) -> int:
    """Count of `symbol`'s stop-outs on today's UTC calendar date.

    NOTE (checked during red-team review, judged acceptable): "today" is
    the UTC calendar date, not the US market's trading-session date. For
    US equities during regular session hours these coincide (regular
    session in UTC never crosses UTC midnight), so this is NOT a live bug
    for this project's current universe. It would matter for a genuinely
    24-hour-adjacent instrument or extended-hours activity very close to
    UTC midnight (roughly 8pm ET / 00:00 UTC); if this project ever adds
    such symbols, switch this to the US market calendar's session date
    (pandas_market_calendars, already a project dependency per the
    HANDOVER's V1 decision record) rather than the raw UTC date.
    """
    journal = _load_stopout_journal(path)
    events = journal.get(symbol.upper(), [])
    today = datetime.now(timezone.utc).date()
    count = 0
    for iso in events:
        try:
            t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t.astimezone(timezone.utc).date() == today:
                count += 1
        except (ValueError, TypeError):
            continue
    return count


def is_whipsaw_blocked(symbol: str, limit: int = WHIPSAW_STOPOUT_LIMIT,
                        path: Optional[Path] = None) -> bool:
    """True if `symbol` has hit `limit` (default 2) same-day stop-outs and
    should be benched for the rest of the trading day."""
    return stopout_count_today(symbol, path) >= limit


def reset_stopout_journal(path: Optional[Path] = None) -> None:
    """Clear the stop-out journal. Operator/test use only."""
    p = Path(path) if path else Path(STOPOUT_JOURNAL_PATH)
    if p.exists():
        p.unlink()

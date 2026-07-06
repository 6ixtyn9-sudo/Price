"""Hard risk limits for the paper-trading exploration layer.

This module is the only thing standing between `monitor.py`'s "in state"
signal and `trading.py`'s `submit_entry` call. A signal that breaches
ANY of these limits is logged with `blocked_by_risk=True` and is NOT
passed to the order router.

It does NOT:
- decide when to trade (that's `monitor.scan_all_slices`)
- place orders (that's `trading.submit_entry`)
- claim any edge or guarantee any outcome

The HANDOVER's "no execution in v1-v4" boundary still applies. This
file exists to make a *deliberate, time-boxed* deviation safe, not to
promote it.
"""

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from price.config import DATA_DIR


HALT_FLAG_PATH = DATA_DIR / "HALT_TRADING.flag"
COOLDOWN_JOURNAL_PATH = DATA_DIR / "cooldown_journal.json"


@dataclass
class RiskLimits:
    """Hard limits. All checks must pass for a new entry to be allowed."""

    max_notional_per_position: float = 2500.0
    max_open_positions: int = 4
    max_daily_realized_loss: float = 500.0
    per_symbol_cooldown_seconds: int = 3600
    default_side: str = "buy"  # fallback side; monitor overrides per-slice
    # Direction gate: short entries are BLOCKED here unless explicitly enabled.
    # This is the kill-switch for the short side -- even a validated short
    # candidate cannot reach trading.submit_entry unless allow_shorts=True.
    allow_shorts: bool = False
    # ---- Position sizing knobs (edge- and volatility-aware sizing) ----
    # When True, monitor sizes each matched signal by conviction (derived
    # from the slice's research edge metrics in candidate_leaderboard.csv)
    # rather than equal-notional. Defaults True; when no leaderboard data
    # is present, sizing degrades to neutral conviction (== equal-notional),
    # so enabling this is zero-risk to the live paper book.
    conviction_sizing_enabled: bool = True
    # Fraction of account equity risked per trade at full conviction, used
    # by the volatility rail (Stage B). 0.005 == 0.5% of equity. The rail
    # only activates when account_equity_for_sizing is also set.
    risk_fraction_per_trade: float = 0.005
    # Account equity used for the volatility rail. None disables Stage B
    # (sizing falls back to conviction-weighted notional only). Toward real
    # capital, set this to current account equity (or have the monitor
    # fetch it live).
    account_equity_for_sizing: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskCheckResult:
    """Outcome of one `check_entry(...)` call. `allowed` is the gate."""

    allowed: bool
    reasons: list = field(default_factory=list)
    details: dict = field(default_factory=dict)


def is_halt_flag_set() -> bool:
    """True if the kill-switch flag file exists. Exits still run when set."""
    return Path(HALT_FLAG_PATH).exists()


def set_halt_flag() -> Path:
    """Touch the kill-switch file. Idempotent."""
    Path(HALT_FLAG_PATH).touch()
    return Path(HALT_FLAG_PATH)


def clear_halt_flag() -> bool:
    """Remove the kill-switch file. Returns True if a flag was removed."""
    p = Path(HALT_FLAG_PATH)
    if p.exists():
        p.unlink()
        return True
    return False


def _load_cooldown_state() -> dict:
    """{symbol: last_entry_iso_utc}. Persisted to disk so it survives restarts."""
    if not Path(COOLDOWN_JOURNAL_PATH).exists():
        return {}
    try:
        with open(COOLDOWN_JOURNAL_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cooldown_state(state: dict) -> None:
    Path(COOLDOWN_JOURNAL_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(COOLDOWN_JOURNAL_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _seconds_since(iso_ts: str) -> Optional[float]:
    try:
        then = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - then).total_seconds()
    except (ValueError, TypeError):
        return None


def check_entry(
    symbol: str,
    qty: int,
    price: float,
    limits: RiskLimits,
    open_positions: list,
    today_realized_pnl: float,
    side: str = "long",
) -> RiskCheckResult:
    """Run ALL risk checks. Returns allowed=True only if every one passes.

    Parameters
    ----------
    symbol : str
        Ticker to be entered (e.g. "SPY").
    qty : int
        Number of shares to buy.
    price : float
        Reference price for notional calc.
    limits : RiskLimits
        The active limit set.
    open_positions : list
        List of currently open position dicts. Each must have at least
        'symbol', 'qty', 'market_value' or 'avg_entry_price'.
    today_realized_pnl : float
        Sum of realized P&L for the current UTC day (negative = loss).
    side : str
        "long" or "short". Short entries are blocked unless
        limits.allow_shorts is True.
    """
    reasons: list = []
    details: dict = {
        "symbol": symbol,
        "qty": qty,
        "price": price,
        "side": side,
        "notional": qty * price,
        "open_position_count": len(open_positions),
        "today_realized_pnl": today_realized_pnl,
    }

    if is_halt_flag_set():
        reasons.append(f"halt flag is set at {HALT_FLAG_PATH}; new entries blocked")

    if side == "short" and not limits.allow_shorts:
        reasons.append("shorts not enabled (RiskLimits.allow_shorts is False)")

    notional = qty * price
    if notional > limits.max_notional_per_position:
        reasons.append(
            f"notional ${notional:.2f} > max ${limits.max_notional_per_position:.2f}"
        )

    distinct_symbols = {p.get("symbol", "").upper() for p in open_positions}
    if symbol.upper() in distinct_symbols:
        reasons.append(f"already have an open position in {symbol}")
    elif len(distinct_symbols) >= limits.max_open_positions:
        reasons.append(f"already at max open positions ({limits.max_open_positions})")

    if -today_realized_pnl >= limits.max_daily_realized_loss:
        reasons.append(
            f"daily realized loss ${-today_realized_pnl:.2f} "
            f">= max ${limits.max_daily_realized_loss:.2f}"
        )

    cooldown_state = _load_cooldown_state()
    last_entry_iso = cooldown_state.get(symbol.upper())
    if last_entry_iso is not None:
        elapsed = _seconds_since(last_entry_iso)
        if elapsed is not None and elapsed < limits.per_symbol_cooldown_seconds:
            reasons.append(
                f"cooldown active for {symbol}: "
                f"{elapsed:.0f}s since last entry "
                f"< {limits.per_symbol_cooldown_seconds}s"
            )

    return RiskCheckResult(allowed=(len(reasons) == 0), reasons=reasons, details=details)


def record_entry(symbol: str) -> None:
    """Stamp `now()` as the last entry time for `symbol`. Call this only
    AFTER an entry has been accepted (i.e. order submitted, not blocked)."""
    state = _load_cooldown_state()
    state[symbol.upper()] = datetime.now(timezone.utc).isoformat()
    _save_cooldown_state(state)


def reset_cooldowns() -> None:
    """Clear the cooldown journal. For operator / test use only."""
    if Path(COOLDOWN_JOURNAL_PATH).exists():
        Path(COOLDOWN_JOURNAL_PATH).unlink()

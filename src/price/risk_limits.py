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
import os
from pathlib import Path as _Path

def _resolve_data_path(env_name: str, default_name: str) -> _Path:
    custom = os.getenv(env_name)
    if custom:
        return _Path(custom)
    return DATA_DIR / default_name

HALT_FLAG_PATH = _resolve_data_path("HALT_FLAG_PATH", "HALT_TRADING.flag")
COOLDOWN_JOURNAL_PATH = _resolve_data_path("COOLDOWN_JOURNAL_PATH", "cooldown_journal.json")

# Slice filter fields considered "transient" for risk-grouping: they flip
# every bar (or every session) and therefore do NOT define a durable
# correlation between two positions. Mirrors position_manager.TRANSIENT_FIELDS
# so the exit policy and the allocation gate agree on what "stable" means.
TRANSIENT_RISK_FIELDS: set = {"state_session", "state_dow", "state_month"}


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
    # ---- Capital allocation knob (correlation-aware) ----
    # Max concurrent open positions that share a risk group. A risk group is
    # the slice's stable entry condition (see risk_group_key): two positions
    # entered on the same condition are bets on the same regime and are
    # treated as correlated exposure, NOT independent slots. Default 2 allows
    # a confirming second name in a family but blocks the whole book
    # concentrating on one factor (e.g. XOP+XLB+KLAC all on
    # stretched_down+downtrend). <= 0 disables the group cap (legacy
    # behaviour: every symbol counts as its own independent slot).
    max_positions_per_risk_group: int = 2
    # ---- Protective-stop knobs (R-based "small losses, large profits") ----
    # Initial stop distance in multiples of ATR(14), set the moment a
    # position is filled. This is what actually makes the volatility
    # rail's dollar-risk math true -- previously nothing enforced it.
    stop_atr_multiple: float = 2.0
    # Chandelier trailing-stop distance (multiples of ATR(14)), active only
    # once a trade has reached +1R. Looser than the entry stop on purpose,
    # so a real trend has room to run instead of being capped.
    trail_atr_multiple: float = 3.0
    # Unrealized R-multiple that triggers the move-to-breakeven ratchet.
    breakeven_trigger_r: float = 1.0
    # Max aggregate open risk (sum of every open position's CURRENT stop
    # distance, in dollars) across the whole book at once, as a fraction of
    # equity. This is the leverage prerequisite: with every position
    # carrying a real stop and the aggregate capped, leverage changes how
    # much notional expresses a given R, not how much can be lost if wrong.
    # None disables the cap (fails open; consistent with every other lever
    # in this project only activating when the data to enforce it exists).
    max_aggregate_open_risk_pct: Optional[float] = 0.03
    # Same-day consecutive stop-outs on one symbol before the whipsaw
    # circuit breaker benches it for the rest of the trading day. Tight
    # stops mean more stop-outs; this exists so "small losses" doesn't
    # quietly become "many small losses in one choppy day." <= 0 disables.
    whipsaw_stopout_limit: int = 2
    # ---- Leverage knobs (steady-state / overnight-hold only) ----
    # How much of the account's real margin capacity to actually use, as a
    # multiple of equity. 1.0 (default) == cash-secured, today's exact
    # behaviour. 2.0 == standard Reg T overnight margin (2x buying power).
    # Deliberately NOT set to Alpaca's 4x intraday multiplier: that rate is
    # intraday-only and automatically steps down to 2x for anything held
    # overnight, and this system's exit policy (5-bar horizon, multi-day
    # holds) does not flatten positions same-day. Using 4.0 here would
    # silently violate the overnight limit every session and invite a
    # broker margin call / forced liquidation -- the exact uncontrolled
    # exit the R-based stop system exists to prevent. True intraday 4x
    # requires a separate same-day force-flatten exit mode; not built here.
    target_leverage_multiple: float = 1.0
    # Real-time margin safety cushion: block new entries once the broker's
    # actual buying_power falls below margin_cushion_pct of the account's
    # theoretical max buying power (equity * target_leverage_multiple).
    # 0.20 == stop entries at 80% margin usage (20% cushion left). This is
    # the honest backstop against our own approximate notional math: it
    # reads Alpaca's real-time account state rather than trusting our
    # arithmetic alone. None disables the check (fails open, consistent
    # with every other equity-dependent lever in this project).
    margin_cushion_pct: Optional[float] = 0.20

    def to_dict(self) -> dict:
        return asdict(self)


def risk_group_key(symbol: str, slice_combination: str) -> str:
    """Normalized risk-group key for a (symbol, slice) pair.

    The key is the slice's STABLE entry condition -- the non-transient
    state fields, sorted into a canonical string so field order in the
    slice_combination text does not matter. Rationale: two positions whose
    entry conditions are identical fire on the same bars by construction
    and are therefore maximally correlated -- they are the same regime bet.
    Grouping on the entry condition needs no correlation-matrix estimation
    (which would itself be an overfit risk) and is self-maintaining, because
    the group is derived from the slice definition rather than a hand-kept
    sector map.

    XOP / XLB / KLAC all carry state_ext=stretched_down + state_slope=
    downtrend, so they collapse to one group. XLF (stretched_up+flat),
    XLK 1d (cross_TLT...+neutral), XLK 1h (cross_USO...+stretched_down),
    and SPY 1h (slope=downtrend, session is transient) each form their own.

    Falls back to the uppercased symbol when the slice cannot be parsed or
    has no stable fields (so an unparseable slice is treated as its own
    singleton group, never as matching everything).
    """
    from price.validation import parse_slice_combination
    try:
        filt = parse_slice_combination(slice_combination)
    except (ValueError, TypeError):
        return symbol.upper()
    stable = {k: v for k, v in filt.items() if k not in TRANSIENT_RISK_FIELDS}
    if not stable:
        return symbol.upper()
    return " + ".join(f"{k}={v}" for k, v in sorted(stable.items()))


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
    symbol_risk_group: Optional[str] = None,
    open_position_risk_groups: Optional[dict] = None,
    proposed_r_dollars: Optional[float] = None,
    open_stop_states: Optional[dict] = None,
    equity_for_risk_cap: Optional[float] = None,
    open_positions_notional: Optional[float] = None,
    buying_power: Optional[float] = None,
) -> RiskCheckResult:
    """Run ALL risk checks. Returns allowed=True only if every one passes.

    symbol_risk_group / open_position_risk_groups drive the correlation-aware
    allocation cap (max_positions_per_risk_group). Both optional for backward
    compatibility; when either is absent the group check is skipped.

    proposed_r_dollars / open_stop_states / equity_for_risk_cap drive the
    aggregate open-risk budget (limits.max_aggregate_open_risk_pct) -- the
    leverage prerequisite. All optional for backward compatibility; when
    any is absent the aggregate-risk check is skipped (fails open, same
    doctrine as every other data-dependent lever in this project).

    open_positions_notional / buying_power (together with equity_for_risk_
    cap) drive the two leverage budgets: the gross notional exposure cap
    (limits.target_leverage_multiple) and the real-time margin cushion
    (limits.margin_cushion_pct). Both optional for backward compatibility;
    when the relevant inputs are absent, that check fails open.
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
        "risk_group": symbol_risk_group,
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

    # Correlation-aware allocation: cap concurrent exposure to one entry
    # condition. Orthogonal to the per-symbol and max-open checks above.
    if (
        limits.max_positions_per_risk_group > 0
        and symbol_risk_group
        and open_position_risk_groups
    ):
        group_count = sum(
            1 for g in open_position_risk_groups.values()
            if g == symbol_risk_group
        )
        if group_count >= limits.max_positions_per_risk_group:
            reasons.append(
                f"risk group '{symbol_risk_group}' at cap "
                f"({group_count}/{limits.max_positions_per_risk_group})"
            )

    if -today_realized_pnl >= limits.max_daily_realized_loss:
        reasons.append(
            f"daily realized loss ${-today_realized_pnl:.2f} "
            f">= max ${limits.max_daily_realized_loss:.2f}"
        )

    # Aggregate open-risk budget: the leverage prerequisite. Blocks a new
    # entry if the SUM of every open position's current stop-distance risk
    # (positions already at breakeven-or-better contribute zero) plus this
    # trade's own R would exceed max_aggregate_open_risk_pct of equity.
    if getattr(limits, "max_aggregate_open_risk_pct", None):
        from price.stops import check_aggregate_risk_budget
        risk_allowed, risk_detail = check_aggregate_risk_budget(
            proposed_r_dollars=proposed_r_dollars,
            states=open_stop_states or {},
            equity=equity_for_risk_cap,
            max_aggregate_open_risk_pct=limits.max_aggregate_open_risk_pct,
        )
        details["aggregate_risk"] = risk_detail
        if not risk_allowed:
            reasons.append(
                f"aggregate open risk ${risk_detail.get('projected_open_risk_dollars'):.2f} "
                f"would exceed budget ${risk_detail.get('budget_dollars'):.2f} "
                f"({limits.max_aggregate_open_risk_pct:.1%} of equity)"
            )

    # Gross notional exposure cap: the leverage budget that the R-based
    # aggregate-risk check above does NOT cover. Leverage changes how much
    # NOTIONAL a given amount of equity can control, not how much R a
    # given stop distance risks -- a low-ATR%, high-priced name can carry
    # a small R while still deploying huge notional/margin exposure.
    # check_gross_notional_budget fails open when equity/notional data is
    # absent, so this is a no-op for every existing caller that doesn't
    # pass open_positions_notional (backward compatible by construction).
    from price.leverage import check_gross_notional_budget
    notional_allowed, notional_detail = check_gross_notional_budget(
        proposed_notional=notional,
        open_positions_notional=open_positions_notional,
        equity=equity_for_risk_cap,
        target_leverage_multiple=getattr(limits, "target_leverage_multiple", 1.0),
    )
    details["gross_notional"] = notional_detail
    if not notional_allowed:
        reasons.append(
            f"gross notional ${notional_detail.get('projected_open_notional'):.2f} "
            f"would exceed budget ${notional_detail.get('budget_notional'):.2f} "
            f"({getattr(limits, 'target_leverage_multiple', 1.0)}x equity)"
        )

    # Margin cushion: real-time broker-truth backstop against our own
    # approximate notional math and against ever approaching a real
    # margin call / forced liquidation.
    if getattr(limits, "margin_cushion_pct", None):
        from price.leverage import check_margin_cushion
        margin_allowed, margin_detail = check_margin_cushion(
            buying_power=buying_power,
            equity=equity_for_risk_cap,
            target_leverage_multiple=getattr(limits, "target_leverage_multiple", 1.0),
            margin_cushion_pct=limits.margin_cushion_pct,
        )
        details["margin_cushion"] = margin_detail
        if not margin_allowed:
            reasons.append(
                f"margin cushion breached: {margin_detail.get('remaining_fraction'):.1%} of "
                f"self-imposed leverage ceiling remains as buying power "
                f"(< {limits.margin_cushion_pct:.0%} required)"
            )

    # Whipsaw circuit breaker: bench a symbol for the rest of the trading
    # day after `whipsaw_stopout_limit` same-day stop-outs. Tight ATR stops
    # mean more stop-outs; this exists so "small losses" cannot silently
    # become "many small losses in one choppy day."
    if getattr(limits, "whipsaw_stopout_limit", 0) and limits.whipsaw_stopout_limit > 0:
        from price.stops import is_whipsaw_blocked, stopout_count_today
        if is_whipsaw_blocked(symbol, limit=limits.whipsaw_stopout_limit):
            reasons.append(
                f"whipsaw circuit breaker: {stopout_count_today(symbol)} stop-outs "
                f"today for {symbol} >= limit {limits.whipsaw_stopout_limit}; "
                "benched for the rest of the trading day"
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

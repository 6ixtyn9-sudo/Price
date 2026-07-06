"""Tests for the risk_limits.check_entry additions that back steady-state
(overnight-hold) leverage:

  - Gross notional exposure cap (RiskLimits.target_leverage_multiple).
  - Real-time margin cushion (RiskLimits.margin_cushion_pct).

Both are additive / optional-kwarg, so backward compatibility with every
existing check_entry call site (levers 1-5, the R-based stop system) is
pinned here too.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.risk_limits import RiskLimits, check_entry  # noqa: E402


def _limits(**overrides):
    return RiskLimits(**overrides)


# ---------------------------------------------------------------------------
# Backward compatibility: default RiskLimits (1.0x, no leverage) is a no-op
# for both new checks when the new kwargs are omitted.
# ---------------------------------------------------------------------------

def test_default_limits_leverage_checks_inert_without_new_kwargs():
    limits = _limits()  # target_leverage_multiple=1.0, margin_cushion_pct=0.20 (default)
    result = check_entry(
        symbol="ANY", qty=1000, price=1000.0, limits=limits,  # huge notional
        open_positions=[], today_realized_pnl=0.0,
    )
    # No open_positions_notional/buying_power/equity passed -> both leverage
    # checks fail open regardless of the notional size. Only the flat
    # max_notional_per_position cap (a pre-existing, unrelated check) can
    # still block this -- so allow for that expected reason only.
    if not result.allowed:
        assert all("gross notional" not in r and "margin cushion" not in r for r in result.reasons)


# ---------------------------------------------------------------------------
# Gross notional exposure cap
# ---------------------------------------------------------------------------

def test_gross_notional_cap_blocks_when_leverage_budget_exceeded():
    limits = _limits(target_leverage_multiple=1.0, max_notional_per_position=100000.0)
    result = check_entry(
        symbol="NEW", qty=10, price=100.0, limits=limits,  # proposed notional = $1000
        open_positions=[], today_realized_pnl=0.0,
        equity_for_risk_cap=1000.0,          # budget = 1.0 * 1000 = $1000
        open_positions_notional=500.0,       # 500 + 1000 = 1500 > 1000
    )
    assert result.allowed is False
    assert any("gross notional" in r for r in result.reasons)
    assert "gross_notional" in result.details


def test_leverage_multiple_of_2x_doubles_the_notional_budget():
    """The core leverage behaviour: the SAME trade that is blocked at 1.0x
    is allowed at 2.0x, for the same equity."""
    limits_1x = _limits(target_leverage_multiple=1.0, max_notional_per_position=100000.0)
    limits_2x = _limits(target_leverage_multiple=2.0, max_notional_per_position=100000.0)

    kwargs = dict(
        symbol="NEW", qty=10, price=100.0,
        open_positions=[], today_realized_pnl=0.0,
        equity_for_risk_cap=1000.0, open_positions_notional=500.0,
    )
    result_1x = check_entry(limits=limits_1x, **kwargs)
    result_2x = check_entry(limits=limits_2x, **kwargs)

    assert result_1x.allowed is False
    assert result_2x.allowed is True


def test_gross_notional_cap_inert_when_open_notional_not_supplied():
    """A caller that hasn't computed open_positions_notional yet must not
    incidentally trip this check just because equity happens to be set
    (e.g. for the unrelated volatility-rail sizing lever)."""
    limits = _limits(target_leverage_multiple=1.0, max_notional_per_position=100000.0)
    result = check_entry(
        symbol="NEW", qty=1000, price=100.0, limits=limits,  # huge notional
        open_positions=[], today_realized_pnl=0.0,
        equity_for_risk_cap=1000.0,  # set, but open_positions_notional is NOT
    )
    assert not any("gross notional" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# Margin cushion
# ---------------------------------------------------------------------------

def test_margin_cushion_blocks_when_buying_power_too_low():
    limits = _limits(target_leverage_multiple=2.0, margin_cushion_pct=0.20,
                      max_notional_per_position=100000.0)
    result = check_entry(
        symbol="NEW", qty=1, price=10.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
        equity_for_risk_cap=1000.0,   # ceiling = 2.0 * 1000 = 2000
        buying_power=200.0,           # remaining = 200/2000 = 0.10 < 0.20
    )
    assert result.allowed is False
    assert any("margin cushion" in r for r in result.reasons)
    assert "margin_cushion" in result.details


def test_margin_cushion_allows_with_healthy_buying_power():
    limits = _limits(target_leverage_multiple=2.0, margin_cushion_pct=0.20,
                      max_notional_per_position=100000.0)
    result = check_entry(
        symbol="NEW", qty=1, price=10.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
        equity_for_risk_cap=1000.0,   # ceiling = 2000
        buying_power=1500.0,          # remaining = 0.75 >= 0.20
    )
    assert result.allowed is True


def test_margin_cushion_disabled_when_pct_is_none():
    limits = _limits(target_leverage_multiple=2.0, margin_cushion_pct=None,
                      max_notional_per_position=100000.0)
    result = check_entry(
        symbol="NEW", qty=1, price=10.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
        equity_for_risk_cap=1000.0, buying_power=0.0,  # would fail if the check were active
    )
    assert not any("margin cushion" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# Both leverage checks compose with the pre-existing checks (aggregate R,
# whipsaw, notional cap, cooldown, etc.) rather than replacing them.
# ---------------------------------------------------------------------------

def test_leverage_checks_compose_with_flat_notional_cap():
    """A trade can clear the leverage budgets but still be blocked by the
    pre-existing flat max_notional_per_position cap, and vice versa --
    they are independent, ALL-must-pass checks."""
    limits = _limits(
        target_leverage_multiple=5.0,  # generous leverage budget
        max_notional_per_position=500.0,  # but a tight flat per-trade cap
    )
    result = check_entry(
        symbol="NEW", qty=10, price=100.0, limits=limits,  # notional = $1000 > $500 cap
        open_positions=[], today_realized_pnl=0.0,
        equity_for_risk_cap=100000.0, open_positions_notional=0.0,
    )
    assert result.allowed is False
    assert any("max $500" in r for r in result.reasons)
    assert not any("gross notional" in r for r in result.reasons)

"""Tests for price.leverage: the gross-notional exposure cap and the
real-time margin-cushion backstop that make steady-state (overnight-hold)
leverage safe -- the two budgets that price.stops's R-based aggregate-
risk check does NOT cover (a low-ATR%%, high-priced name can carry a
tiny R while deploying huge notional/margin exposure).

All pure functions: no network, no broker client.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.leverage import (  # noqa: E402
    check_gross_notional_budget,
    check_margin_cushion,
    total_open_notional,
)


# ---------------------------------------------------------------------------
# total_open_notional
# ---------------------------------------------------------------------------

def test_total_open_notional_prefers_market_value():
    positions = [
        {"symbol": "XOP", "qty": 16, "avg_entry_price": 100.0, "market_value": 1650.0},
    ]
    assert total_open_notional(positions) == pytest.approx(1650.0)


def test_total_open_notional_falls_back_to_qty_times_entry_price():
    positions = [
        {"symbol": "XOP", "qty": 16, "avg_entry_price": 100.0},
    ]
    assert total_open_notional(positions) == pytest.approx(1600.0)


def test_total_open_notional_sums_across_positions():
    positions = [
        {"symbol": "XOP", "qty": 16, "avg_entry_price": 100.0, "market_value": 1650.0},
        {"symbol": "XLF", "qty": 10, "avg_entry_price": 58.0, "market_value": 590.0},
    ]
    assert total_open_notional(positions) == pytest.approx(2240.0)


def test_total_open_notional_empty_list():
    assert total_open_notional([]) == 0.0
    assert total_open_notional(None) == 0.0


def test_total_open_notional_skips_malformed_row_without_raising():
    positions = [
        {"symbol": "BAD", "qty": "not-a-number", "avg_entry_price": None},
        {"symbol": "XOP", "qty": 16, "avg_entry_price": 100.0, "market_value": 1650.0},
    ]
    assert total_open_notional(positions) == pytest.approx(1650.0)


# ---------------------------------------------------------------------------
# check_gross_notional_budget
# ---------------------------------------------------------------------------

def test_gross_notional_blocks_when_over_budget():
    allowed, detail = check_gross_notional_budget(
        proposed_notional=3000.0,
        open_positions_notional=8000.0,
        equity=10000.0,
        target_leverage_multiple=1.0,  # budget = $10,000; 8000+3000=11000 > 10000
    )
    assert allowed is False
    assert detail["projected_open_notional"] == pytest.approx(11000.0)
    assert detail["budget_notional"] == pytest.approx(10000.0)


def test_gross_notional_allows_leverage_to_expand_budget():
    """The whole point of leverage: 2x multiple doubles the budget for the
    SAME equity, so a trade that would be blocked at 1x is allowed at 2x."""
    allowed, _ = check_gross_notional_budget(
        proposed_notional=3000.0,
        open_positions_notional=8000.0,
        equity=10000.0,
        target_leverage_multiple=2.0,  # budget = $20,000; 8000+3000=11000 <= 20000
    )
    assert allowed is True


def test_gross_notional_allows_when_under_budget():
    allowed, _ = check_gross_notional_budget(
        proposed_notional=1000.0,
        open_positions_notional=0.0,
        equity=10000.0,
        target_leverage_multiple=1.0,
    )
    assert allowed is True


def test_gross_notional_fails_open_on_missing_data():
    allowed, _ = check_gross_notional_budget(1000.0, 0.0, equity=None, target_leverage_multiple=1.0)
    assert allowed is True
    allowed, _ = check_gross_notional_budget(1000.0, 0.0, equity=10000.0, target_leverage_multiple=None)
    assert allowed is True
    allowed, _ = check_gross_notional_budget(None, 0.0, equity=10000.0, target_leverage_multiple=1.0)
    assert allowed is True
    allowed, _ = check_gross_notional_budget(1000.0, 0.0, equity=10000.0, target_leverage_multiple=0.0)
    assert allowed is True


def test_gross_notional_fails_open_when_open_notional_not_provided():
    """open_positions_notional=None means the caller never computed it --
    the check must stay inert (opt-in), not silently assume zero exposure."""
    allowed, detail = check_gross_notional_budget(
        proposed_notional=5000.0, open_positions_notional=None,
        equity=10000.0, target_leverage_multiple=1.0,
    )
    assert allowed is True
    assert "inactive" in detail["reason"]


def test_gross_notional_activates_with_explicit_zero_open_notional():
    """Explicit 0.0 (a genuinely empty book) DOES activate the check --
    only None (unknown / not computed) is treated as opt-out."""
    allowed, detail = check_gross_notional_budget(
        proposed_notional=5000.0, open_positions_notional=0.0,
        equity=10000.0, target_leverage_multiple=1.0,
    )
    assert allowed is True
    assert detail["current_open_notional"] == 0.0


# ---------------------------------------------------------------------------
# check_margin_cushion
# ---------------------------------------------------------------------------

def test_margin_cushion_allows_with_plenty_of_buying_power():
    # ceiling = 2.0 * 10000 = 20000; buying_power=15000 -> remaining=0.75 >= 0.20
    allowed, detail = check_margin_cushion(
        buying_power=15000.0, equity=10000.0,
        target_leverage_multiple=2.0, margin_cushion_pct=0.20,
    )
    assert allowed is True
    assert detail["remaining_fraction"] == pytest.approx(0.75)


def test_margin_cushion_blocks_at_80_percent_usage():
    # ceiling = 20000; buying_power=3000 -> remaining=0.15 < 0.20 cushion required
    allowed, detail = check_margin_cushion(
        buying_power=3000.0, equity=10000.0,
        target_leverage_multiple=2.0, margin_cushion_pct=0.20,
    )
    assert allowed is False
    assert detail["remaining_fraction"] == pytest.approx(0.15)


def test_margin_cushion_boundary_exactly_at_cushion_is_allowed():
    # remaining_fraction == margin_cushion_pct exactly -> allowed (>=)
    allowed, _ = check_margin_cushion(
        buying_power=4000.0, equity=10000.0,
        target_leverage_multiple=2.0, margin_cushion_pct=0.20,  # remaining = 4000/20000 = 0.20
    )
    assert allowed is True


def test_margin_cushion_fails_open_on_missing_data():
    allowed, _ = check_margin_cushion(None, 10000.0, 2.0, 0.20)
    assert allowed is True
    allowed, _ = check_margin_cushion(3000.0, None, 2.0, 0.20)
    assert allowed is True
    allowed, _ = check_margin_cushion(3000.0, 10000.0, None, 0.20)
    assert allowed is True
    allowed, _ = check_margin_cushion(3000.0, 10000.0, 2.0, None)
    assert allowed is True
    allowed, _ = check_margin_cushion(3000.0, 10000.0, 0.0, 0.20)
    assert allowed is True
    allowed, _ = check_margin_cushion(3000.0, 10000.0, 2.0, 0.0)
    assert allowed is True


def test_margin_cushion_normalizes_against_our_own_multiple_not_brokers():
    """A broker might grant 4x intraday, but the cushion check must use
    OUR self-imposed target_leverage_multiple (e.g. 2.0), not whatever
    higher multiple the broker's raw buying_power might reflect."""
    # Same buying_power, but our own ceiling is smaller (1.0x) -> the same
    # dollar amount represents a SMALLER remaining fraction of our budget.
    allowed_at_1x, detail_1x = check_margin_cushion(
        buying_power=5000.0, equity=10000.0,
        target_leverage_multiple=1.0, margin_cushion_pct=0.20,
    )
    allowed_at_4x, detail_4x = check_margin_cushion(
        buying_power=5000.0, equity=10000.0,
        target_leverage_multiple=4.0, margin_cushion_pct=0.20,
    )
    assert detail_1x["remaining_fraction"] == pytest.approx(0.5)
    assert detail_4x["remaining_fraction"] == pytest.approx(0.125)
    assert allowed_at_1x is True
    assert allowed_at_4x is False

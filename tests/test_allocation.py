"""Tests for correlation-aware capital allocation (lever 3).

Covers:
  - risk_group_key: stable-condition grouping, order-independence, transient
    fields excluded, cross_ fields retained, fallbacks.
  - check_entry group cap: blocks the Nth same-group position, allows
    different-group positions, disabled when cap <= 0 or args absent.
  - The real Tier-1 concentration case: XOP+XLB+KLAC collapse to ONE group.

Pure unit tests on risk_group_key (no I/O); check_entry tests use a
hand-built RiskLimits + position lists (no network, no credentials).
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.risk_limits import RiskLimits, check_entry, risk_group_key  # noqa: E402


# ---------------------------------------------------------------------------
# risk_group_key (pure)
# ---------------------------------------------------------------------------

def test_xop_xlb_klac_collapse_to_one_group():
    """The headline case: three different symbols, same stretched_down+
    downtrend condition -> one risk group. This is the concentration lever 3
    exists to address."""
    def g(sym):
        return risk_group_key(sym, "state_ext=stretched_down + state_slope=downtrend")
    assert g("XOP") == g("XLB") == g("KLAC")
    # And it is the sorted stable-condition string, symbol-independent.
    assert g("XOP") == "state_ext=stretched_down + state_slope=downtrend"


def test_different_conditions_are_different_groups():
    a = risk_group_key("XLF", "state_ext=stretched_up + state_slope=flat")
    b = risk_group_key("XOP", "state_ext=stretched_down + state_slope=downtrend")
    assert a != b


def test_group_is_field_order_independent():
    """'ext=A + slope=B' and 'slope=B + ext=A' must hash to the same group."""
    g1 = risk_group_key("SPY", "state_ext=stretched_up + state_slope=flat")
    g2 = risk_group_key("SPY", "state_slope=flat + state_ext=stretched_up")
    assert g1 == g2


def test_transient_fields_excluded():
    """session is transient -> SPY 1h afternoon+downtrend groups on slope
    alone, so an evening+downtrend SPY would be the SAME group (correct:
    same regime bet, different session)."""
    afternoon = risk_group_key("SPY", "state_session=afternoon + state_slope=downtrend")
    evening = risk_group_key("SPY", "state_session=evening + state_slope=downtrend")
    assert afternoon == evening == "state_slope=downtrend"


def test_cross_fields_retained_in_group():
    """Cross-asset conditioning fields are stable -> retained, so
    cross_TLT and cross_USO variants are distinct groups."""
    a = risk_group_key("XLK", "cross_TLT_state_slope=uptrend + state_ext=neutral")
    b = risk_group_key("XLK", "cross_USO_state_vol=mid_vol + state_ext=stretched_down")
    assert a != b


def test_group_fallback_on_unparseable():
    # Garbage slice -> symbol-only singleton group (never matches everything).
    assert risk_group_key("QQQ", "not a real slice") == "QQQ"


def test_group_fallback_on_transient_only():
    # Only transient fields -> no stable condition -> symbol-only group.
    assert risk_group_key("SPY", "state_session=afternoon") == "SPY"


def test_monitored_set_groups_as_expected():
    """The 7 monitored slices collapse to 5 groups, with XOP/XLB/KLAC the
    single multi-member group. Pins the design rationale from the handover."""
    slices = {
        "XLB": "state_ext=stretched_down + state_slope=downtrend",
        "XOP": "state_ext=stretched_down + state_slope=downtrend",
        "KLAC": "state_ext=stretched_down + state_slope=downtrend",
        "SPY": "state_session=afternoon + state_slope=downtrend",
        "XLK_1d": "cross_TLT_state_slope=uptrend + state_ext=neutral",
        "XLK_1h": "cross_USO_state_vol=mid_vol + state_ext=stretched_down",
        "XLF": "state_ext=stretched_up + state_slope=flat",
    }
    groups = {}
    for k, sc in slices.items():
        groups.setdefault(risk_group_key(k, sc), []).append(k)
    assert len(groups) == 5
    multi = [g for g, members in groups.items() if len(members) > 1]
    assert len(multi) == 1
    assert set(groups[multi[0]]) == {"XLB", "XOP", "KLAC"}


# ---------------------------------------------------------------------------
# check_entry group cap
# ---------------------------------------------------------------------------

def _pos(sym):
    """check_entry expects position dicts (it calls p.get('symbol'))."""
    return {"symbol": sym, "qty": 10, "market_value": 1000.0}


def _limits(max_per_group=2, max_open=7):
    return RiskLimits(
        max_notional_per_position=2500.0,
        max_open_positions=max_open,
        max_positions_per_risk_group=max_per_group,
    )


SD = "state_ext=stretched_down + state_slope=downtrend"
SU = "state_ext=stretched_up + state_slope=flat"


def test_group_cap_blocks_third_same_group():
    """XOP + XLB already open in the stretched_down+downtrend group; KLAC
    (same group) is blocked; XLF (different group) is allowed."""
    limits = _limits(max_per_group=2)
    open_positions = [_pos("XOP"), _pos("XLB")]
    open_groups = {"XOP": risk_group_key("XOP", SD), "XLB": risk_group_key("XLB", SD)}

    r_block = check_entry("KLAC", 10, 100.0, limits, open_positions, 0.0,
                          symbol_risk_group=risk_group_key("KLAC", SD),
                          open_position_risk_groups=open_groups)
    assert not r_block.allowed
    assert any("risk group" in x and "at cap" in x for x in r_block.reasons)

    r_allow = check_entry("XLF", 10, 100.0, limits, open_positions, 0.0,
                          symbol_risk_group=risk_group_key("XLF", SU),
                          open_position_risk_groups=open_groups)
    assert r_allow.allowed


def test_group_cap_allows_within_limit():
    """One open in group -> second same-group entry allowed (cap=2)."""
    limits = _limits(max_per_group=2)
    open_positions = [_pos("XOP")]
    open_groups = {"XOP": risk_group_key("XOP", SD)}
    r = check_entry("XLB", 10, 100.0, limits, open_positions, 0.0,
                    symbol_risk_group=risk_group_key("XLB", SD),
                    open_position_risk_groups=open_groups)
    assert r.allowed


def test_group_cap_disabled_when_zero():
    """max_per_group=0 -> no group check (legacy: every symbol independent)."""
    limits = _limits(max_per_group=0)
    open_positions = [_pos("XOP"), _pos("XLB")]
    open_groups = {"XOP": risk_group_key("XOP", SD), "XLB": risk_group_key("XLB", SD)}
    r = check_entry("KLAC", 10, 100.0, limits, open_positions, 0.0,
                    symbol_risk_group=risk_group_key("KLAC", SD),
                    open_position_risk_groups=open_groups)
    assert r.allowed  # would be blocked under cap=2


def test_group_cap_skipped_when_args_absent():
    """Backward compat: no group args -> behaves like legacy (no group check)."""
    limits = _limits(max_per_group=2)
    open_positions = [_pos("XOP"), _pos("XLB")]
    r = check_entry("KLAC", 10, 100.0, limits, open_positions, 0.0)
    assert r.allowed


def test_group_cap_orthogonal_to_max_open():
    """max_open=2 already reached via two DIFFERENT groups -> third blocked
    by max_open, not by the group cap. Confirms the checks are independent."""
    limits = _limits(max_per_group=2, max_open=2)
    open_positions = [_pos("XOP"), _pos("XLF")]
    open_groups = {"XOP": risk_group_key("XOP", SD), "XLF": risk_group_key("XLF", SU)}
    r = check_entry("XLB", 10, 100.0, limits, open_positions, 0.0,
                    symbol_risk_group=risk_group_key("XLB", SD),
                    open_position_risk_groups=open_groups)
    assert not r.allowed
    assert any("max open positions" in x for x in r.reasons)
    # Group cap did NOT also fire (XLB's group has only XOP open = count 1 < 2).
    assert not any("risk group" in x for x in r.reasons)


def test_risk_group_in_details_and_signal():
    """The candidate's risk group is surfaced in the check details for audit."""
    limits = _limits()
    r = check_entry("XLF", 10, 100.0, limits, [], 0.0,
                    symbol_risk_group="state_ext=stretched_up + state_slope=flat",
                    open_position_risk_groups={})
    assert r.details["risk_group"] == "state_ext=stretched_up + state_slope=flat"


def test_group_cap_counts_only_matching_group():
    """Two open positions in OTHER groups do not consume a different group's cap."""
    limits = _limits(max_per_group=1)
    open_positions = [_pos("XLF"), _pos("SPY")]
    open_groups = {
        "XLF": risk_group_key("XLF", SU),
        "SPY": risk_group_key("SPY", "state_session=afternoon + state_slope=downtrend"),
    }
    # XOP's group (stretched_down+downtrend) has 0 open -> allowed even at cap=1.
    r = check_entry("XOP", 10, 100.0, limits, open_positions, 0.0,
                    symbol_risk_group=risk_group_key("XOP", SD),
                    open_position_risk_groups=open_groups)
    assert r.allowed

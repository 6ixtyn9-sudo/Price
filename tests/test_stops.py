"""Tests for price.stops: the R-based protective-stop / trailing-stop /
aggregate-risk-budget / whipsaw-breaker logic.

All pure-function / pure-dataclass tests: no network, no broker client,
no warehouse. Persistence tests use tmp_path so they never touch the
real localdata/ files.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.stops import (  # noqa: E402
    DEFAULT_STOP_ATR_MULT,
    aggregate_open_risk_dollars,
    check_aggregate_risk_budget,
    compute_initial_stop,
    is_whipsaw_blocked,
    load_stop_states,
    new_stop_state,
    record_stopout,
    remove_stop_state,
    reset_stopout_journal,
    save_stop_states,
    stopout_count_today,
    update_trailing_stop,
)


# ---------------------------------------------------------------------------
# compute_initial_stop / new_stop_state
# ---------------------------------------------------------------------------

def test_initial_stop_long_is_below_entry_by_k_atr():
    stop_price, r = compute_initial_stop(100.0, atr=2.0, side="long", k_stop=2.0)
    assert stop_price == pytest.approx(96.0)
    assert r == pytest.approx(4.0)


def test_initial_stop_short_is_above_entry_by_k_atr():
    stop_price, r = compute_initial_stop(100.0, atr=2.0, side="short", k_stop=2.0)
    assert stop_price == pytest.approx(104.0)
    assert r == pytest.approx(4.0)


def test_initial_stop_uses_default_k_stop_of_2x_atr():
    stop_price, r = compute_initial_stop(50.0, atr=1.0, side="long")
    assert r == pytest.approx(DEFAULT_STOP_ATR_MULT * 1.0)
    assert stop_price == pytest.approx(50.0 - DEFAULT_STOP_ATR_MULT)


@pytest.mark.parametrize("bad_price", [0.0, -5.0, float("nan"), None])
def test_initial_stop_rejects_bad_entry_price(bad_price):
    with pytest.raises(ValueError):
        compute_initial_stop(bad_price, atr=1.0, side="long")


@pytest.mark.parametrize("bad_atr", [0.0, -1.0, float("nan"), None])
def test_initial_stop_rejects_bad_atr(bad_atr):
    with pytest.raises(ValueError):
        compute_initial_stop(100.0, atr=bad_atr, side="long")


def test_initial_stop_rejects_bad_side():
    with pytest.raises(ValueError):
        compute_initial_stop(100.0, atr=1.0, side="sideways")


def test_new_stop_state_long_shape():
    st = new_stop_state("xop", "long", qty=16, entry_price=154.47, atr=3.0,
                         stop_order_id="abc123")
    assert st.symbol == "XOP"
    assert st.side == "long"
    assert st.stage == "initial"
    assert st.current_stop_price == pytest.approx(154.47 - 2 * 3.0)
    assert st.initial_stop_price == st.current_stop_price
    assert st.r_per_share == pytest.approx(6.0)
    assert st.initial_r_dollars == pytest.approx(6.0 * 16)
    assert st.extreme_price == pytest.approx(154.47)
    assert st.stop_order_id == "abc123"


# ---------------------------------------------------------------------------
# current_risk_dollars / unrealized_r_multiple
# ---------------------------------------------------------------------------

def test_current_risk_dollars_long_before_any_ratchet_equals_initial_r():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)
    assert st.current_risk_dollars() == pytest.approx(st.initial_r_dollars)


def test_current_risk_dollars_zero_once_stop_at_or_past_breakeven():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)
    st.current_stop_price = 100.0  # breakeven
    assert st.current_risk_dollars() == pytest.approx(0.0)
    st.current_stop_price = 101.0  # better than breakeven
    assert st.current_risk_dollars() == pytest.approx(0.0)


def test_unrealized_r_multiple_long():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)  # R=4
    assert st.unrealized_r_multiple(104.0) == pytest.approx(1.0)
    assert st.unrealized_r_multiple(108.0) == pytest.approx(2.0)
    assert st.unrealized_r_multiple(96.0) == pytest.approx(-1.0)


def test_unrealized_r_multiple_short():
    st = new_stop_state("SPY", "short", qty=10, entry_price=100.0, atr=2.0)  # R=4
    assert st.unrealized_r_multiple(96.0) == pytest.approx(1.0)
    assert st.unrealized_r_multiple(104.0) == pytest.approx(-1.0)


def test_unrealized_r_multiple_none_on_degenerate_r():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)
    st.r_per_share = 0.0
    assert st.unrealized_r_multiple(110.0) is None


# ---------------------------------------------------------------------------
# update_trailing_stop -- the core "small losses, large profits" behaviour
# ---------------------------------------------------------------------------

def test_stop_unchanged_below_breakeven_trigger():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)  # R=4, stop=96
    updated = update_trailing_stop(st, current_price=103.0, atr=2.0)  # +0.75R
    assert updated.current_stop_price == pytest.approx(96.0)
    assert updated.stage == "initial"


def test_stop_ratchets_to_breakeven_at_exactly_1r():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)  # R=4
    updated = update_trailing_stop(st, current_price=104.0, atr=None)  # +1.0R, no ATR
    assert updated.current_stop_price == pytest.approx(100.0)
    assert updated.stage == "breakeven"


def test_stop_never_loosens_past_breakeven_even_if_price_pulls_back():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)  # R=4, stop=96
    st = update_trailing_stop(st, current_price=104.0, atr=None)  # breakeven at 100
    # Price pulls back to +0.5R -- must NOT loosen the stop back down.
    st2 = update_trailing_stop(st, current_price=102.0, atr=None)
    assert st2.current_stop_price == pytest.approx(100.0)
    assert st2.stage == "breakeven"


def test_stop_trails_chandelier_once_past_1r_and_atr_available():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)  # R=4
    # Price runs to 112 (extreme), chandelier = 112 - 3*2 = 106 > breakeven(100)
    updated = update_trailing_stop(st, current_price=112.0, atr=2.0, k_trail=3.0)
    assert updated.current_stop_price == pytest.approx(106.0)
    assert updated.stage == "trailing"
    assert updated.extreme_price == pytest.approx(112.0)


def test_stop_trail_tracks_new_extreme_and_never_gives_back_more_than_k_trail_atr():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)
    st = update_trailing_stop(st, current_price=112.0, atr=2.0, k_trail=3.0)  # stop=106
    # Price runs further to 120 -- stop should trail up to 120-6=114.
    st2 = update_trailing_stop(st, current_price=120.0, atr=2.0, k_trail=3.0)
    assert st2.current_stop_price == pytest.approx(114.0)
    # Price pulls back to 116 (still above stop) -- stop must NOT move down.
    st3 = update_trailing_stop(st2, current_price=116.0, atr=2.0, k_trail=3.0)
    assert st3.current_stop_price == pytest.approx(114.0)
    assert st3.extreme_price == pytest.approx(120.0)  # extreme also never regresses


def test_short_side_trail_mirrors_long():
    st = new_stop_state("SPY", "short", qty=10, entry_price=100.0, atr=2.0)  # R=4, stop=108
    updated = update_trailing_stop(st, current_price=88.0, atr=2.0, k_trail=3.0)
    # +3R move; extreme=88; chandelier = 88 + 3*2 = 94 < breakeven(100)
    assert updated.current_stop_price == pytest.approx(94.0)
    assert updated.stage == "trailing"


def test_update_returns_new_object_does_not_mutate_input():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)
    original_stop = st.current_stop_price
    updated = update_trailing_stop(st, current_price=104.0, atr=None)
    assert st.current_stop_price == pytest.approx(original_stop)  # unchanged
    assert updated is not st
    assert updated.current_stop_price != st.current_stop_price


def test_update_handles_degenerate_r_gracefully():
    st = new_stop_state("SPY", "long", qty=10, entry_price=100.0, atr=2.0)
    st.r_per_share = 0.0
    updated = update_trailing_stop(st, current_price=110.0, atr=2.0)
    assert updated is st  # no-op, no crash


def test_update_handles_none_state_gracefully():
    assert update_trailing_stop(None, current_price=100.0, atr=1.0) is None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "stop_state.json"
    st = new_stop_state("XOP", "long", qty=16, entry_price=154.47, atr=3.0,
                         stop_order_id="order-1")
    save_stop_states({"XOP": st}, path=path)
    loaded = load_stop_states(path=path)
    assert set(loaded.keys()) == {"XOP"}
    assert loaded["XOP"].entry_price == pytest.approx(154.47)
    assert loaded["XOP"].stop_order_id == "order-1"


def test_load_missing_file_returns_empty_dict(tmp_path):
    assert load_stop_states(path=tmp_path / "nope.json") == {}


def test_load_corrupt_file_returns_empty_dict_not_raise(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    assert load_stop_states(path=p) == {}


def test_remove_stop_state(tmp_path):
    path = tmp_path / "stop_state.json"
    st = new_stop_state("XOP", "long", qty=16, entry_price=154.47, atr=3.0)
    save_stop_states({"XOP": st}, path=path)
    assert remove_stop_state("xop", path=path) is True
    assert load_stop_states(path=path) == {}
    assert remove_stop_state("xop", path=path) is False


# ---------------------------------------------------------------------------
# Aggregate open-risk budget (the leverage prerequisite)
# ---------------------------------------------------------------------------

def test_aggregate_open_risk_sums_current_risk_across_positions():
    a = new_stop_state("XOP", "long", qty=10, entry_price=100.0, atr=2.0)   # R=4/sh -> $40
    b = new_stop_state("XLB", "long", qty=5, entry_price=50.0, atr=1.0)    # R=2/sh -> $10
    total = aggregate_open_risk_dollars({"XOP": a, "XLB": b})
    assert total == pytest.approx(50.0)


def test_aggregate_open_risk_excludes_breakeven_or_better_positions():
    a = new_stop_state("XOP", "long", qty=10, entry_price=100.0, atr=2.0)  # $40 at risk
    b = new_stop_state("XLB", "long", qty=5, entry_price=50.0, atr=1.0)
    b.current_stop_price = 50.0  # ratcheted to breakeven -> contributes $0
    total = aggregate_open_risk_dollars({"XOP": a, "XLB": b})
    assert total == pytest.approx(40.0)


def test_check_aggregate_risk_budget_blocks_when_over_cap():
    a = new_stop_state("XOP", "long", qty=10, entry_price=100.0, atr=2.0)  # $40 at risk
    allowed, detail = check_aggregate_risk_budget(
        proposed_r_dollars=20.0,
        states={"XOP": a},
        equity=1000.0,
        max_aggregate_open_risk_pct=0.03,  # $30 budget
    )
    assert allowed is False
    assert detail["projected_open_risk_dollars"] == pytest.approx(60.0)
    assert detail["budget_dollars"] == pytest.approx(30.0)


def test_check_aggregate_risk_budget_allows_when_under_cap():
    allowed, detail = check_aggregate_risk_budget(
        proposed_r_dollars=10.0,
        states={},
        equity=1000.0,
        max_aggregate_open_risk_pct=0.03,
    )
    assert allowed is True


def test_check_aggregate_risk_budget_fails_open_on_missing_data():
    allowed, _ = check_aggregate_risk_budget(10.0, {}, equity=None, max_aggregate_open_risk_pct=0.03)
    assert allowed is True
    allowed, _ = check_aggregate_risk_budget(10.0, {}, equity=1000.0, max_aggregate_open_risk_pct=None)
    assert allowed is True
    allowed, _ = check_aggregate_risk_budget(None, {}, equity=1000.0, max_aggregate_open_risk_pct=0.03)
    assert allowed is True
    allowed, _ = check_aggregate_risk_budget(10.0, {}, equity=1000.0, max_aggregate_open_risk_pct=0.0)
    assert allowed is True


# ---------------------------------------------------------------------------
# Whipsaw circuit breaker
# ---------------------------------------------------------------------------

def test_whipsaw_not_blocked_with_no_stopouts(tmp_path):
    path = tmp_path / "stopout_journal.json"
    assert is_whipsaw_blocked("XOP", path=path) is False


def test_whipsaw_blocks_after_limit_reached(tmp_path):
    path = tmp_path / "stopout_journal.json"
    record_stopout("XOP", path=path)
    assert is_whipsaw_blocked("XOP", path=path) is False  # 1 stop-out, limit is 2
    record_stopout("XOP", path=path)
    assert is_whipsaw_blocked("XOP", path=path) is True  # 2nd stop-out trips it


def test_whipsaw_is_per_symbol(tmp_path):
    path = tmp_path / "stopout_journal.json"
    record_stopout("XOP", path=path)
    record_stopout("XOP", path=path)
    assert is_whipsaw_blocked("XOP", path=path) is True
    assert is_whipsaw_blocked("XLB", path=path) is False


def test_stopout_count_today_ignores_old_entries(tmp_path):
    import json as _json
    from datetime import datetime, timedelta, timezone as _tz
    path = tmp_path / "stopout_journal.json"
    old_ts = (datetime.now(_tz.utc) - timedelta(days=3)).isoformat()
    path.write_text(_json.dumps({"XOP": [old_ts, old_ts]}))
    assert stopout_count_today("XOP", path=path) == 0
    assert is_whipsaw_blocked("XOP", path=path) is False


def test_reset_stopout_journal(tmp_path):
    path = tmp_path / "stopout_journal.json"
    record_stopout("XOP", path=path)
    reset_stopout_journal(path=path)
    assert stopout_count_today("XOP", path=path) == 0

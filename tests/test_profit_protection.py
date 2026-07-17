import pytest
from datetime import datetime, timezone, timedelta
from price.stops import StopState
from price.profit_protection import (
    ProfitPolicy,
    check_profit_exits,
    is_crypto_symbol,
    is_futures_symbol,
    minutes_to_ny_close,
)

def _mock_stop(side="long", entry_price=100.0, r_per_share=2.0, extreme_price=None):
    return StopState(
        symbol="TEST",
        side=side,
        qty=10,
        entry_price=entry_price,
        initial_stop_price=entry_price - r_per_share,
        current_stop_price=entry_price - r_per_share,
        r_per_share=r_per_share,
        stage="initial",
        extreme_price=extreme_price,
        stop_order_id="123",
    )

def test_take_profit_long_fires():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(take_profit_r=3.0)
    # current_price = 106.0 -> +6.0 -> 3.0R
    exits = check_profit_exits("TEST", "long", 106.0, state, policy)
    assert len(exits) == 1
    assert exits[0]["profit_exit_type"] == "take_profit_r"
    assert exits[0]["unrealized_r"] == 3.0

def test_take_profit_short_fires():
    state = _mock_stop(side="short", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(take_profit_r=3.0)
    # current_price = 94.0 -> +6.0 -> 3.0R
    exits = check_profit_exits("TEST", "short", 94.0, state, policy)
    assert len(exits) == 1
    assert exits[0]["profit_exit_type"] == "take_profit_r"
    assert exits[0]["unrealized_r"] == 3.0

def test_take_profit_not_yet():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(take_profit_r=3.0)
    # current_price = 105.0 -> +5.0 -> 2.5R
    exits = check_profit_exits("TEST", "long", 105.0, state, policy)
    assert len(exits) == 0

def test_take_profit_disabled():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(take_profit_r=None)
    exits = check_profit_exits("TEST", "long", 110.0, state, policy)
    assert len(exits) == 0

def test_giveback_fires():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0, extreme_price=105.0)
    # peak was +5.0 -> 2.5R
    policy = ProfitPolicy(giveback_trigger_r=2.0, max_giveback_r=1.0)
    # current_price = 102.0 -> +2.0 -> 1.0R. Giveback = 1.5R.
    exits = check_profit_exits("TEST", "long", 102.0, state, policy)
    assert len(exits) == 1
    assert exits[0]["profit_exit_type"] == "profit_giveback"
    assert exits[0]["unrealized_r"] == 1.0
    assert exits[0]["max_unrealized_r"] == 2.5
    assert exits[0]["giveback_r"] == 1.5

def test_giveback_trigger_not_reached():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0, extreme_price=103.0)
    # peak was +3.0 -> 1.5R
    policy = ProfitPolicy(giveback_trigger_r=2.0, max_giveback_r=0.5)
    # current_price = 100.0 -> +0.0 -> 0.0R. Giveback = 1.5R.
    exits = check_profit_exits("TEST", "long", 100.0, state, policy)
    assert len(exits) == 0

def test_giveback_insufficient():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0, extreme_price=105.0)
    # peak was +5.0 -> 2.5R
    policy = ProfitPolicy(giveback_trigger_r=2.0, max_giveback_r=1.0)
    # current_price = 104.0 -> +4.0 -> 2.0R. Giveback = 0.5R.
    exits = check_profit_exits("TEST", "long", 104.0, state, policy)
    assert len(exits) == 0

def test_giveback_disabled():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0, extreme_price=105.0)
    policy = ProfitPolicy(giveback_trigger_r=None, max_giveback_r=0.5)
    exits = check_profit_exits("TEST", "long", 102.0, state, policy)
    assert len(exits) == 0

def test_extreme_price_none_graceful():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0, extreme_price=None)
    policy = ProfitPolicy(giveback_trigger_r=2.0, max_giveback_r=1.0)
    exits = check_profit_exits("TEST", "long", 102.0, state, policy)
    assert len(exits) == 0

def test_eod_fires_equity(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(eod_profit_lock_r=0.75, eod_lock_minutes_before_close=45)
    # Mock minutes_to_ny_close to return 30
    monkeypatch.setattr("price.profit_protection.minutes_to_ny_close", lambda x: 30)
    # current_price = 102.0 -> +2.0 -> 1.0R (>= 0.75R)
    exits = check_profit_exits("XLF", "long", 102.0, state, policy, now_utc=datetime.now(timezone.utc))
    assert len(exits) == 1
    assert exits[0]["profit_exit_type"] == "eod_profit_lock"

def test_eod_not_fired_outside_window(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(eod_profit_lock_r=0.75, eod_lock_minutes_before_close=45)
    monkeypatch.setattr("price.profit_protection.minutes_to_ny_close", lambda x: 90)
    exits = check_profit_exits("XLF", "long", 102.0, state, policy, now_utc=datetime.now(timezone.utc))
    assert len(exits) == 0

def test_eod_fires_exactly_at_window(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(eod_profit_lock_r=0.75, eod_lock_minutes_before_close=45)
    monkeypatch.setattr("price.profit_protection.minutes_to_ny_close", lambda x: 45.0)
    exits = check_profit_exits("XLF", "long", 102.0, state, policy, now_utc=datetime.now(timezone.utc))
    assert len(exits) == 1

def test_eod_no_fire_on_crypto(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(eod_profit_lock_r=0.75, eod_lock_minutes_before_close=45, apply_eod_to_crypto=False)
    monkeypatch.setattr("price.profit_protection.minutes_to_ny_close", lambda x: 30)
    exits = check_profit_exits("BTC/USD", "long", 102.0, state, policy, now_utc=datetime.now(timezone.utc))
    assert len(exits) == 0

def test_eod_no_fire_on_futures(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(eod_profit_lock_r=0.75, eod_lock_minutes_before_close=45, apply_eod_to_futures=False)
    monkeypatch.setattr("price.profit_protection.minutes_to_ny_close", lambda x: 30)
    exits = check_profit_exits("FUT/NQ", "long", 102.0, state, policy, now_utc=datetime.now(timezone.utc))
    assert len(exits) == 0

def test_eod_no_fire_on_weekend(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(eod_profit_lock_r=0.75, eod_lock_minutes_before_close=45)
    monkeypatch.setattr("price.profit_protection.minutes_to_ny_close", lambda x: None)
    exits = check_profit_exits("XLF", "long", 102.0, state, policy, now_utc=datetime.now(timezone.utc))
    assert len(exits) == 0

def test_eod_no_fire_below_threshold(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy(eod_profit_lock_r=0.75, eod_lock_minutes_before_close=45)
    monkeypatch.setattr("price.profit_protection.minutes_to_ny_close", lambda x: 30)
    # current_price = 101.0 -> +1.0 -> 0.5R
    exits = check_profit_exits("XLF", "long", 101.0, state, policy, now_utc=datetime.now(timezone.utc))
    assert len(exits) == 0

def test_missing_stop_state_no_exit():
    policy = ProfitPolicy()
    exits = check_profit_exits("XLF", "long", 102.0, None, policy)
    assert len(exits) == 0

def test_degenerate_r_no_exit():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=0.0)
    policy = ProfitPolicy()
    exits = check_profit_exits("XLF", "long", 102.0, state, policy)
    assert len(exits) == 0

def test_nan_price_no_exit():
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0)
    policy = ProfitPolicy()
    exits = check_profit_exits("XLF", "long", float('nan'), state, policy)
    assert len(exits) == 0

def test_multiple_conditions_can_fire(monkeypatch):
    state = _mock_stop(side="long", entry_price=100.0, r_per_share=2.0, extreme_price=107.0) # 3.5R
    policy = ProfitPolicy(take_profit_r=2.0, giveback_trigger_r=2.0, max_giveback_r=1.0)
    # current_price = 104.0 -> +4.0 -> 2.0R. Take profit is hit AND Giveback (1.5R >= 1.0R) is hit.
    exits = check_profit_exits("TEST", "long", 104.0, state, policy)
    assert len(exits) == 2
    types = [e["profit_exit_type"] for e in exits]
    assert "take_profit_r" in types
    assert "profit_giveback" in types

def test_minutes_to_ny_close_market_open():
    # 2026-07-17 is a Friday (valid trading day)
    # 15:00 ET -> 19:00 UTC (summer)
    dt = datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc)
    mins = minutes_to_ny_close(dt)
    assert mins is not None
    assert mins == 60.0

def test_minutes_to_ny_close_after_close():
    # 16:30 ET -> 20:30 UTC
    dt = datetime(2026, 7, 17, 20, 30, tzinfo=timezone.utc)
    mins = minutes_to_ny_close(dt)
    assert mins is None

def test_minutes_to_ny_close_holiday():
    # 2026-07-03 is Friday before July 4th (Independence Day obs) -> Holiday
    dt = datetime(2026, 7, 3, 19, 0, tzinfo=timezone.utc)
    mins = minutes_to_ny_close(dt)
    assert mins is None

def test_minutes_to_ny_close_weekend():
    # 2026-07-18 is Saturday
    dt = datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc)
    mins = minutes_to_ny_close(dt)
    assert mins is None

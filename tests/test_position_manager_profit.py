from typing import Dict, Optional
import pandas as pd
import pytest

from price.position_manager import check_exits, ExitPolicy
from price.profit_protection import ProfitPolicy
from price.stops import StopState

# Mocks
def mock_load_entry_context():
    return {
        "AAPL": {
            "slice_combination": "state_slope=uptrend",
            "timeframe": "1d",
            "entry_bar_ts": "2023-01-01T00:00:00Z"
        }
    }

def mock_load_from_warehouse(symbol: str, timeframe: str):
    return pd.DataFrame({
        "bar_ts_utc": pd.date_range("2023-01-01", periods=60, tz="UTC"),
        "close": [100.0] * 60,
        "high": [101.0] * 60,
        "low": [99.0] * 60,
        "open": [100.0] * 60,
        "volume": [1000] * 60,
    })

def mock_compute_price_features(df):
    df["state_slope"] = "uptrend"
    return df

def mock_apply_state_bins(df, bin_mode):
    return df

def mock_count_bars_after(ts, df):
    return 10

@pytest.fixture
def patch_position_manager(monkeypatch):
    monkeypatch.setattr("price.position_manager._load_entry_context", mock_load_entry_context)
    monkeypatch.setattr("price.position_manager.load_from_warehouse", mock_load_from_warehouse)
    monkeypatch.setattr("price.position_manager.compute_price_features", mock_compute_price_features)
    monkeypatch.setattr("price.position_manager.apply_state_bins", mock_apply_state_bins)
    monkeypatch.setattr("price.position_manager._count_bars_after", mock_count_bars_after)

def test_profit_exit_fields(patch_position_manager, monkeypatch):
    open_positions = pd.DataFrame([{
        "symbol": "AAPL",
        "current_price": 130.0,
        "qty": 10
    }])
    open_position_slice_labels = {"AAPL": "state_slope=uptrend"}
    
    policy = ExitPolicy(
        horizon_bars=5,
        profit_policy=ProfitPolicy(take_profit_r=3.0)
    )
    
    stop_states = {
        "AAPL": StopState(
            symbol="AAPL",
            side="long",
            entry_price=100.0,
            initial_stop_price=90.0,
            qty=10, current_stop_price=90.0,
            r_per_share=10.0,
            extreme_price=130.0
        )
    }
    monkeypatch.setattr("price.stops.load_stop_states", lambda: stop_states)
    
    intents = check_exits(open_positions, open_position_slice_labels, exit_policy=policy)
    
    assert len(intents) == 1
    intent = intents[0]
    
    assert intent["action"] == "exit"
    assert "take_profit" in intent["reason"]
    assert intent["profit_exit_type"] == "take_profit_r"
    assert intent["profit_unrealized_r"] == 3.0
    assert intent["profit_max_unrealized_r"] == 3.0
    assert intent["profit_giveback_r"] == 0.0
    assert intent["profit_entry_price"] == 100.0
    assert intent["profit_r_per_share"] == 10.0
    assert intent["profit_exit_count"] == 1

def test_missing_stop_state_preserves_legacy(patch_position_manager, monkeypatch):
    open_positions = pd.DataFrame([{
        "symbol": "AAPL",
        "current_price": 130.0,
        "qty": 10
    }])
    open_position_slice_labels = {"AAPL": "state_slope=uptrend"}
    
    policy = ExitPolicy(
        horizon_bars=5,
        profit_policy=ProfitPolicy(take_profit_r=3.0)
    )
    
    monkeypatch.setattr("price.stops.load_stop_states", lambda: {})
    
    intents = check_exits(open_positions, open_position_slice_labels, exit_policy=policy)
    
    assert len(intents) == 1
    intent = intents[0]
    
    assert intent["action"] == "exit"
    assert "horizon reached" in intent["reason"]
    assert "profit_exit_type" not in intent

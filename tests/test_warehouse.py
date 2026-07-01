import pytest
import pandas as pd
from datetime import datetime, timezone

import price.warehouse

@pytest.fixture
def temp_warehouse(tmp_path):
    old_dir = price.warehouse.WAREHOUSE_DIR
    price.warehouse.WAREHOUSE_DIR = tmp_path
    yield tmp_path
    price.warehouse.WAREHOUSE_DIR = old_dir

def test_save_and_load_warehouse(temp_warehouse):
    df = pd.DataFrame({
        'symbol': ['SPY', 'SPY'],
        'timeframe': ['1d', '1d'],
        'bar_ts_utc': [
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 2, tzinfo=timezone.utc)
        ],
        'source': ['tiingo', 'tiingo'],
        'ingested_at_utc': [datetime.now(timezone.utc), datetime.now(timezone.utc)],
        'open_raw': [100.0, 101.0],
        'high_raw': [102.0, 103.0],
        'low_raw': [99.0, 100.0],
        'close_raw': [101.5, 102.5],
        'volume_raw': [10000, 11000],
        'open_adj': [100.0, 101.0],
        'high_adj': [102.0, 103.0],
        'low_adj': [99.0, 100.0],
        'close_adj': [101.5, 102.5],
        'adj_factor': [1.0, 1.0],
        'split_factor': [1.0, 1.0],
        'dividend_cash': [0.0, 0.0]
    })
    
    price.warehouse.save_to_warehouse(df)
    partition_file = temp_warehouse / "symbol=SPY" / "timeframe=1d" / "data.parquet"
    assert partition_file.exists()
    
    loaded = price.warehouse.load_from_warehouse('SPY', '1d')
    assert len(loaded) == 2
    assert loaded.loc[0, 'close_raw'] == 101.5

def test_warehouse_revision_overwrite(temp_warehouse):
    df_v1 = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['1d'],
        'bar_ts_utc': [datetime(2026, 6, 1, tzinfo=timezone.utc)],
        'source': ['tiingo'],
        'ingested_at_utc': [datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)],
        'open_raw': [100.0], 'high_raw': [102.0], 'low_raw': [99.0], 'close_raw': [101.5], 'volume_raw': [10000],
        'open_adj': [100.0], 'high_adj': [102.0], 'low_adj': [99.0], 'close_adj': [101.5], 'adj_factor': [1.0],
        'split_factor': [1.0], 'dividend_cash': [0.0]
    })
    price.warehouse.save_to_warehouse(df_v1)
    
    df_v2 = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['1d'],
        'bar_ts_utc': [datetime(2026, 6, 1, tzinfo=timezone.utc)],
        'source': ['tiingo'],
        'ingested_at_utc': [datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)],
        'open_raw': [100.0], 'high_raw': [102.0], 'low_raw': [99.0], 'close_raw': [101.99], 'volume_raw': [10500],
        'open_adj': [100.0], 'high_adj': [102.0], 'low_adj': [99.0], 'close_adj': [101.99], 'adj_factor': [1.0],
        'split_factor': [1.0], 'dividend_cash': [0.0]
    })
    price.warehouse.save_to_warehouse(df_v2)
    
    loaded = price.warehouse.load_from_warehouse('SPY', '1d')
    assert len(loaded) == 1
    assert loaded.loc[0, 'close_raw'] == 101.99

def test_resample_15m_to_1h(temp_warehouse):
    df_15m = pd.DataFrame({
        'symbol': ['SPY'] * 4,
        'timeframe': ['15m'] * 4,
        'bar_ts_utc': [
            datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 13, 45, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 14, 15, tzinfo=timezone.utc)
        ],
        'source': ['alpaca'] * 4,
        'ingested_at_utc': [datetime.now(timezone.utc)] * 4,
        'open_raw': [100.0, 101.0, 102.0, 103.0],
        'high_raw': [102.0, 103.0, 104.0, 105.0],
        'low_raw': [99.0, 100.0, 101.0, 102.0],
        'close_raw': [101.0, 102.0, 103.0, 104.0],
        'volume_raw': [100, 200, 300, 400]
    })
    price.warehouse.save_to_warehouse(df_15m)
    
    price.warehouse.resample_15m_to_1h('SPY')
    loaded_1h = price.warehouse.load_from_warehouse('SPY', '1h')
    assert len(loaded_1h) == 2

def test_propagate_adjustment_factors(temp_warehouse):
    df_1d = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['1d'],
        'bar_ts_utc': [datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)],
        'source': ['tiingo'],
        'ingested_at_utc': [datetime.now(timezone.utc)],
        'open_raw': [100.0], 'high_raw': [102.0], 'low_raw': [99.0], 'close_raw': [101.5], 'volume_raw': [10000],
        'open_adj': [98.0], 'high_adj': [99.96], 'low_adj': [97.02], 'close_adj': [99.47],
        'adj_factor': [0.98], 'split_factor': [1.0], 'dividend_cash': [0.50]
    })
    price.warehouse.save_to_warehouse(df_1d)
    
    df_15m = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['15m'],
        'bar_ts_utc': [datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)],
        'source': ['alpaca'],
        'ingested_at_utc': [datetime.now(timezone.utc)],
        'open_raw': [100.0], 'high_raw': [101.0], 'low_raw': [99.5], 'close_raw': [100.5], 'volume_raw': [500]
    })
    price.warehouse.save_to_warehouse(df_15m)
    
    price.warehouse.propagate_adjustment_factors('SPY')
    updated_15m = price.warehouse.load_from_warehouse('SPY', '15m')
    assert len(updated_15m) == 1
    
    row = updated_15m.iloc[0]
    assert row['adj_factor'] == 0.98
    assert row['close_adj'] == 100.5 * 0.98

def test_propagate_adjustment_factors_uses_daily_utc_date_for_market_session(monkeypatch):
    """Daily bars at midnight UTC must map to the same New York market date,
    not the prior New York evening.

    Regression guard for the bug where Tiingo 1d bar_ts_utc was converted to
    America/New_York before extracting the date, shifting daily adjustment
    factors one session early and creating artificial intraday price jumps.
    """
    import pandas as pd
    import price.warehouse as warehouse

    saved = {}

    daily = pd.DataFrame(
        {
            "symbol": ["XYZ"],
            "timeframe": ["1d"],
            "bar_ts_utc": pd.to_datetime(["2024-01-03 00:00:00"], utc=True),
            "adj_factor": [0.5],
            "split_factor": [1.0],
            "dividend_cash": [0.0],
        }
    )

    intraday = pd.DataFrame(
        {
            "symbol": ["XYZ"],
            "timeframe": ["15m"],
            "bar_ts_utc": pd.to_datetime(["2024-01-03 14:30:00"], utc=True),
            "source": ["test"],
            "ingested_at_utc": pd.to_datetime(["2024-01-03 15:00:00"], utc=True),
            "open_raw": [100.0],
            "high_raw": [110.0],
            "low_raw": [90.0],
            "close_raw": [104.0],
            "volume_raw": [1000],
        }
    )

    def fake_load(symbol, timeframe):
        if timeframe == "1d":
            return daily.copy()
        if timeframe == "15m":
            return intraday.copy()
        return pd.DataFrame()

    def fake_save(df):
        saved[(df["symbol"].iloc[0], df["timeframe"].iloc[0])] = df.copy()

    monkeypatch.setattr(warehouse, "load_from_warehouse", fake_load)
    monkeypatch.setattr(warehouse, "save_to_warehouse", fake_save)

    warehouse.propagate_adjustment_factors("XYZ")

    adjusted = saved[("XYZ", "15m")]
    assert adjusted["adj_factor"].iloc[0] == 0.5
    assert adjusted["close_adj"].iloc[0] == 52.0


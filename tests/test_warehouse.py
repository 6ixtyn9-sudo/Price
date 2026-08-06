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


# ─────────────────────────────────────────────────────────────────────────────
# Adjustment-book integrity gate (daily frames)
#
# A booked corporate action (split_factor != 1 and/or dividend_cash > 0) must
# move adj_factor; a material adj_factor step must carry a booked action. The
# gate lives at save_to_warehouse so every ingest path (build, capture,
# manual recovery) funnels through it. A poisoned daily frame is refused
# persistence and written to a JSONL quarantine ledger. Fail-open doctrine:
# unauditable inputs (missing columns, <2 bars, junk boundaries) are clean.
# ─────────────────────────────────────────────────────────────────────────────

import json as _json  # noqa: E402
from datetime import timedelta as _timedelta  # noqa: E402

from price.warehouse import adjustment_integrity_violations  # noqa: E402


def _adj_bar(i, adj_f=1.0, split=1.0, div=0.0, close=100.0, symbol="TEST", tf="1d"):
    ts = datetime(2026, 7, 1, tzinfo=timezone.utc) + _timedelta(days=i)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "bar_ts_utc": ts,
        "open_raw": close, "high_raw": close * 1.01,
        "low_raw": close * 0.99, "close_raw": close,
        "volume_raw": 1_000_000,
        "open_adj": close * adj_f, "high_adj": close * 1.01 * adj_f,
        "low_adj": close * 0.99 * adj_f, "close_adj": close * adj_f,
        "adj_factor": adj_f, "split_factor": split, "dividend_cash": div,
    }


def test_booked_split_with_flat_adj_factor_flagged():
    df = pd.DataFrame([_adj_bar(0), _adj_bar(1, split=3.0, close=33.33), _adj_bar(2, close=33.50)])
    reasons = adjustment_integrity_violations(df)
    assert any("booked_event_factor_mismatch" in r for r in reasons)


def test_booked_split_with_matching_factor_passes():
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0, close=99.0),
        _adj_bar(1, adj_f=3.0, split=3.0, close=33.33),
        _adj_bar(2, adj_f=3.0, close=33.50),
    ])
    assert adjustment_integrity_violations(df) == []


def test_booked_special_dividend_with_flat_adj_factor_flagged():
    # $12 special on $100: a -12% ex-date gap that sits BELOW the 35% crash
    # detector — precisely the silent-poison case this gate must catch at ingest.
    df = pd.DataFrame([
        _adj_bar(0, close=100.0),
        _adj_bar(1, div=12.0, close=88.3),
        _adj_bar(2, close=88.8),
    ])
    reasons = adjustment_integrity_violations(df)
    assert any("booked_event_factor_mismatch" in r for r in reasons)


def test_booked_dividend_with_matching_factor_passes():
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0, close=100.0),
        _adj_bar(1, adj_f=0.88, div=12.0, close=88.3),
        _adj_bar(2, adj_f=0.88, close=88.8),
    ])
    assert adjustment_integrity_violations(df) == []


def test_immaterial_dividend_flat_factor_tolerated():
    df = pd.DataFrame([
        _adj_bar(0, close=100.0),
        _adj_bar(1, div=0.50, close=99.6),
        _adj_bar(2, close=99.9),
    ])
    assert adjustment_integrity_violations(df) == []


def test_immaterial_dividend_with_matching_small_step_passes():
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0, close=100.0),
        _adj_bar(1, adj_f=0.995, div=0.50, close=99.6),
        _adj_bar(2, adj_f=0.995, close=99.9),
    ])
    assert adjustment_integrity_violations(df) == []


def test_unbooked_material_adj_factor_step_flagged():
    df = pd.DataFrame([_adj_bar(0, adj_f=1.0), _adj_bar(1, adj_f=0.90), _adj_bar(2, adj_f=0.90)])
    reasons = adjustment_integrity_violations(df)
    assert any("unbooked_adj_factor_step" in r for r in reasons)


def test_unbooked_minor_factor_noise_tolerated():
    df = pd.DataFrame([_adj_bar(0, adj_f=1.0), _adj_bar(1, adj_f=0.995), _adj_bar(2, adj_f=0.995)])
    assert adjustment_integrity_violations(df) == []


def test_clean_frame_no_violations():
    df = pd.DataFrame([_adj_bar(i, close=100.0 + i * 0.5) for i in range(5)])
    assert adjustment_integrity_violations(df) == []


def test_single_row_frame_is_unauditable_and_clean():
    df = pd.DataFrame([_adj_bar(0, split=3.0)])
    assert adjustment_integrity_violations(df) == []


def test_missing_adj_factor_column_is_unauditable_and_clean():
    df = pd.DataFrame([_adj_bar(0), _adj_bar(1, split=3.0, close=33.3)]).drop(columns=["adj_factor"])
    assert adjustment_integrity_violations(df) == []


def test_junk_factor_boundary_ignored_not_flagged():
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0),
        _adj_bar(1, adj_f=0.0),
        _adj_bar(2, adj_f=1.0),
    ])
    assert adjustment_integrity_violations(df) == []


def test_save_to_warehouse_refuses_poisoned_daily_frame(temp_warehouse):
    poison = pd.DataFrame([
        _adj_bar(0, symbol="POISON", close=100.0),
        _adj_bar(1, symbol="POISON", div=12.0, close=88.3),
        _adj_bar(2, symbol="POISON", close=88.8),
    ])
    price.warehouse.save_to_warehouse(poison)
    loaded = price.warehouse.load_from_warehouse("POISON", "1d")
    assert loaded.empty, "poisoned daily frame must not persist"

    ledger = temp_warehouse / price.warehouse._ADJUSTMENT_QUARANTINE_LEDGER
    assert ledger.exists(), "quarantine ledger must be written"
    record = _json.loads(ledger.read_text().strip().splitlines()[-1])
    assert record["symbol"] == "POISON"
    assert any("booked_event_factor_mismatch" in v for v in record["violations"])


def test_save_to_warehouse_persists_clean_daily_frame(temp_warehouse):
    clean = pd.DataFrame([
        _adj_bar(0, symbol="CLEAN", adj_f=1.0, close=100.0),
        _adj_bar(1, symbol="CLEAN", adj_f=0.88, div=12.0, close=88.3),
        _adj_bar(2, symbol="CLEAN", adj_f=0.88, close=88.8),
    ])
    price.warehouse.save_to_warehouse(clean)
    loaded = price.warehouse.load_from_warehouse("CLEAN", "1d")
    assert len(loaded) == 3


def test_save_to_warehouse_persists_flat_dummy_frame(temp_warehouse):
    # futures/crypto staging writes adj = raw with factors 1.0 and no booked
    # events — flat and honest, must never trip the gate
    dummy = pd.DataFrame([_adj_bar(i, symbol="TEST", close=5000.0 + i) for i in range(4)])
    price.warehouse.save_to_warehouse(dummy)
    loaded = price.warehouse.load_from_warehouse("TEST", "1d")
    assert len(loaded) == 4


def test_intraday_frames_are_not_gated(temp_warehouse):
    intraday = pd.DataFrame([_adj_bar(i, tf="15m", close=100.0 + i * 0.1) for i in range(4)])
    price.warehouse.save_to_warehouse(intraday)
    loaded = price.warehouse.load_from_warehouse("TEST", "15m")
    assert len(loaded) == 4


def test_multi_symbol_save_quarantines_only_the_poisoned_symbol(temp_warehouse):
    poison = pd.DataFrame([
        _adj_bar(0, symbol="POISON", close=100.0),
        _adj_bar(1, symbol="POISON", div=12.0, close=88.3),
    ])
    clean = pd.DataFrame([
        _adj_bar(0, symbol="CLEAN", adj_f=1.0, close=100.0),
        _adj_bar(1, symbol="CLEAN", adj_f=0.88, div=12.0, close=88.3),
    ])
    price.warehouse.save_to_warehouse(pd.concat([poison, clean], ignore_index=True))
    assert price.warehouse.load_from_warehouse("POISON", "1d").empty
    assert len(price.warehouse.load_from_warehouse("CLEAN", "1d")) == 2


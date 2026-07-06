"""Tests for the disk-cached feature frame in build_eligible_frame.

Ensures that the feature cache lookup path matches the Hive partition layout
(symbol={sym}/timeframe={tf}/), that cache hits avoid recomputing features,
and that mtime changes invalidate old cache entries.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from price.warehouse import save_to_warehouse  # noqa: E402
import validate_slices as vs  # noqa: E402


@pytest.fixture
def setup_cache_test(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / "features_cache"
    monkeypatch.setattr(vs, "FEATURES_CACHE_DIR", cache_dir)

    import price.warehouse as wh

    old_dir = wh.WAREHOUSE_DIR
    wh.WAREHOUSE_DIR = tmp_path / "localdata" / "warehouse"
    yield tmp_path, cache_dir
    wh.WAREHOUSE_DIR = old_dir


def make_dummy_df(n=30, symbol="SPY", timeframe="1d"):
    base_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dates = [base_date + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "symbol": [symbol] * n,
        "timeframe": [timeframe] * n,
        "bar_ts_utc": dates,
        "source": ["tiingo"] * n,
        "ingested_at_utc": [datetime.now(timezone.utc)] * n,
        "open_raw": [100.0 + i for i in range(n)],
        "high_raw": [102.0 + i for i in range(n)],
        "low_raw": [99.0 + i for i in range(n)],
        "close_raw": [101.0 + i for i in range(n)],
        "volume_raw": [1000000] * n,
        "adj_factor_split": [1.0] * n,
        "adj_factor_div": [1.0] * n,
        "open_adj": [100.0 + i for i in range(n)],
        "high_adj": [102.0 + i for i in range(n)],
        "low_adj": [99.0 + i for i in range(n)],
        "close_adj": [101.0 + i for i in range(n)],
    })


def test_cache_path_matches_warehouse_layout():
    """Regression test: verify build_eligible_frame uses Hive partition syntax in path."""
    src = Path(vs.__file__).read_text()
    assert "warehouse/symbol={symbol}/timeframe={timeframe}/data.parquet" in src


def test_cache_hit_avoids_recomputation(setup_cache_test):
    """Verify calling build_eligible_frame twice creates 1 cache file and hits on second call."""
    tmp_path, cache_dir = setup_cache_test
    df = make_dummy_df()
    save_to_warehouse(df)

    assert not cache_dir.exists() or len(list(cache_dir.glob("*.parquet"))) == 0

    # First call - should compute features and save to cache
    df1 = vs.build_eligible_frame("SPY", "1d")
    assert not df1.empty
    cache_files = list(cache_dir.glob("*.parquet"))
    assert len(cache_files) == 1

    # Second call - should hit cache (no new files created)
    df2 = vs.build_eligible_frame("SPY", "1d")
    assert not df2.empty
    assert len(list(cache_dir.glob("*.parquet"))) == 1
    pd.testing.assert_frame_equal(df1, df2)


def test_cache_invalidation_on_mtime_change(setup_cache_test):
    """Verify that modifying warehouse file mtime creates a new cache entry."""
    tmp_path, cache_dir = setup_cache_test
    df = make_dummy_df()
    save_to_warehouse(df)

    vs.build_eligible_frame("SPY", "1d")
    cache_files_v1 = list(cache_dir.glob("*.parquet"))
    assert len(cache_files_v1) == 1

    # Modify mtime explicitly by adding 10000 seconds to guarantee distinct mtime integer
    wh_file = tmp_path / "localdata" / "warehouse" / "symbol=SPY" / "timeframe=1d" / "data.parquet"
    old_mtime = wh_file.stat().st_mtime
    new_mtime = old_mtime + 10000.0
    os.utime(wh_file, (new_mtime, new_mtime))

    vs.build_eligible_frame("SPY", "1d")
    cache_files_v2 = list(cache_dir.glob("*.parquet"))
    assert len(cache_files_v2) == 2

    # Verify the new cache key is different
    new_files = set(cache_files_v2) - set(cache_files_v1)
    assert len(new_files) == 1

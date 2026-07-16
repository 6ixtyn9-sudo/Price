import sys
import types
from pathlib import Path

import pandas as pd

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

alpaca = types.ModuleType("alpaca")
alpaca_data = types.ModuleType("alpaca.data")
alpaca_data_historical = types.ModuleType("alpaca.data.historical")
alpaca_data_requests = types.ModuleType("alpaca.data.requests")
alpaca_data_timeframe = types.ModuleType("alpaca.data.timeframe")
alpaca_data_enums = types.ModuleType("alpaca.data.enums")

alpaca_data_historical.StockHistoricalDataClient = object
alpaca_data_historical.CryptoHistoricalDataClient = object
alpaca_data_requests.StockBarsRequest = object
alpaca_data_requests.CryptoBarsRequest = object
alpaca_data_timeframe.TimeFrame = object
alpaca_data_timeframe.TimeFrameUnit = object
alpaca_data_enums.DataFeed = types.SimpleNamespace(IEX="IEX")

sys.modules.setdefault("alpaca", alpaca)
sys.modules.setdefault("alpaca.data", alpaca_data)
sys.modules.setdefault("alpaca.data.historical", alpaca_data_historical)
sys.modules.setdefault("alpaca.data.requests", alpaca_data_requests)
sys.modules.setdefault("alpaca.data.timeframe", alpaca_data_timeframe)
sys.modules.setdefault("alpaca.data.enums", alpaca_data_enums)

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "scripts", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import discover_slices  # noqa: E402
import research_crypto  # noqa: E402
import research_futures  # noqa: E402
from price.data_sources import _build_yfinance_canonical, resolve_universal_source  # noqa: E402
from price.discovery import bin_features  # noqa: E402


def test_resolve_universal_source_uses_yfinance_for_canonical_futures():
    assert resolve_universal_source("FUT/ES", "1d") == "yfinance_futures"
    assert resolve_universal_source("FUT/CL", "1h") == "yfinance_futures"


def test_build_yfinance_canonical_handles_futures_without_adj_close():
    idx = pd.date_range("2026-01-01", periods=3, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [10, 11, 12],
        },
        index=idx,
    )

    out = _build_yfinance_canonical(df, "FUT/ES", "1d")

    assert list(out["symbol"].unique()) == ["FUT/ES"]
    assert (out["close_adj"] == out["close_raw"]).all()
    assert (out["adj_factor"] == 1.0).all()
    assert (out["dividend_cash"] == 0.0).all()
    assert (out["split_factor"] == 1.0).all()


def test_bin_features_emits_crypto_additive_states():
    df = pd.DataFrame(
        {
            "feat_ext_vs_ma_20": [-0.02, 0.0, 0.03],
            "feat_trend_slope_20": [0.1, 0.2, 0.3],
            "feat_realized_vol_20": [0.01, 0.02, 0.03],
            "feat_session_bucket": [0, 1, 2],
            "feat_dow": [0, 2, 4],
            "feat_utc_session_bucket": [0, 1, 2],
            "feat_weekpart": [0, 0, 1],
            "feat_ret_day_equiv": [-0.05, 0.0, 0.07],
            "feat_realized_vol_day_equiv": [0.2, 0.5, 1.0],
        }
    )

    binned = bin_features(df)

    assert list(binned["state_utc_session"]) == ["utc_asia", "utc_europe", "utc_us"]
    assert list(binned["state_weekpart"]) == ["weekday", "weekday", "weekend"]
    assert "state_ret_day" in binned.columns
    assert "state_vol_day" in binned.columns


def test_crypto_isolated_paths_restore_globals(tmp_path: Path):
    original_discover = discover_slices.DISCOVERED_SLICES_PATH
    with research_crypto.isolated_research_paths(tmp_path / "crypto") as paths:
        assert discover_slices.DISCOVERED_SLICES_PATH == str(paths["discovered"])
    assert discover_slices.DISCOVERED_SLICES_PATH == original_discover


def test_futures_isolated_paths_restore_globals(tmp_path: Path):
    original_discover = discover_slices.DISCOVERED_SLICES_PATH
    with research_futures.isolated_research_paths(tmp_path / "futures") as paths:
        assert discover_slices.DISCOVERED_SLICES_PATH == str(paths["discovered"])
    assert discover_slices.DISCOVERED_SLICES_PATH == original_discover


def test_crypto_profile_discovery_matrix_contains_crypto_native_fields():
    combos = discover_slices._build_combinations("1h", cond_symbols=["BTC/USD", "ETH/USD"], profile="crypto")
    assert ["state_utc_session", "state_ext"] in combos
    assert ["state_ret_day", "state_ext", "state_slope"] in combos
    assert ["cross_BTC/USD_state_slope", "state_ext"] in combos


def test_default_profile_discovery_matrix_unchanged():
    combos = discover_slices._build_combinations("1h", cond_symbols=None, profile=None)
    assert ["state_session", "state_ext"] in combos
    assert ["state_utc_session", "state_ext"] not in combos

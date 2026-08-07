import importlib
import sys
import types
from pathlib import Path

import pandas as pd

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))


def _load_root_if_available(pkg_name: str) -> types.ModuleType:
    """Prefer the REAL package root when alpaca-py is installed (it always is,
    via requirements.lock). A bare ModuleType root is not a package: any
    session where THIS file is collected before a module that imports
    price.trading dies with "'alpaca' is not a package". The shared-suite
    order masked that only alphabetically (test_broker_order_backfill
    imports price.trading earlier). Leaf stubs below stay as-is so the
    research modules keep their offline-safe import targets."""
    try:
        return importlib.import_module(pkg_name)
    except Exception:  # pragma: no cover - offline fallback, legacy behaviour
        return types.ModuleType(pkg_name)


def _attach_submodule_path(stub: types.ModuleType, pkg_name: str) -> None:
    """Give a package-level stub the real search path once its parent is the
    real package, so unstubbed submodules resolve for real."""
    if stub is None or hasattr(stub, "__path__"):
        return
    try:
        spec = importlib.util.find_spec(pkg_name)
    except Exception:
        spec = None
    if spec is not None and spec.submodule_search_locations:
        stub.__path__ = list(spec.submodule_search_locations)


alpaca = _load_root_if_available("alpaca")
alpaca_data = types.ModuleType("alpaca.data")
if hasattr(alpaca, "__path__"):
    _attach_submodule_path(alpaca_data, "alpaca.data")
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


# ---------------------------------------------------------------------------
# Lane confinement (2026-08-07): the futures lane submitted real SPY/XLF
# equity orders from stale cache candidates; the crypto lane's book was
# built on a spot broker that cannot short. Lane separation is now a hard
# rule at the broker choke point, not a naming convention on ops files.
# (Broker-path regression pins live in tests/test_trading_stops.py, which
# imports price.trading with a proper fake client.)
# ---------------------------------------------------------------------------

from price.lane_guard import lane_submission_block_reason, normalize_lane  # noqa: E402


def test_lane_guard_equity_lane_shape():
    assert lane_submission_block_reason("eq", "XLF") is None
    assert lane_submission_block_reason("equity", "SPY") is None
    assert "equity lane" in lane_submission_block_reason("eq", "AAVE/USD")
    assert lane_submission_block_reason("eq", "FUT/NQ") is not None


def test_lane_guard_crypto_usd_pairs_only():
    assert lane_submission_block_reason("crypto", "AAVE/USD") is None
    assert lane_submission_block_reason("crypto", "eth/usd") is None
    assert lane_submission_block_reason("crypto", "XLF") is not None
    assert lane_submission_block_reason("crypto", "FUT/NQ") is not None
    # Only USD-quoted spot pairs; other quote currencies are a venue decision
    assert lane_submission_block_reason("crypto", "BTC/USDT") is not None


def test_lane_guard_futures_submits_nothing():
    # Ghost-order regression: futures-dedicated runs placed SPY/XLF equity
    # orders 2026-07-27..2026-08-05. Every symbol is refused on this lane.
    assert "no broker execution path" in lane_submission_block_reason("fut", "SPY")
    assert lane_submission_block_reason("futures", "XLF") is not None
    assert lane_submission_block_reason("fut", "FUT/NQ") is not None


def test_lane_guard_unknown_lane_fails_closed():
    assert "unrecognized lane" in lane_submission_block_reason("options", "SPY")
    assert normalize_lane("fut") == "futures"
    assert normalize_lane("EQ") == "equity"

import sys
import types
from pathlib import Path

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from price.market_profiles import (  # noqa: E402
    get_market_profile,
    infer_market_profile,
)


def test_get_market_profile_defaults():
    crypto = get_market_profile("crypto")
    futures = get_market_profile("futures")

    assert crypto.default_condition_symbols == ("BTC/USD", "ETH/USD")
    assert crypto.default_bin_mode == "rolling"
    assert futures.default_timeframes == ("1d",)
    assert futures.execution_enabled_default is False


def test_infer_market_profile_by_symbol():
    assert infer_market_profile("SPY").name == "equity"
    assert infer_market_profile("BTC/USD").name == "crypto"
    assert infer_market_profile("FUT/ES").name == "futures"

import sys
import types

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from price import universe as u
from price.config import is_crypto, is_equity, is_futures, ETF_SYMBOLS

def test_crypto_detection():
    assert is_crypto("BTC/USD")
    assert is_crypto("ETH/USD")
    assert not is_crypto("SPY")
    assert is_equity("SPY")
    assert not is_equity("BTC/USD")
    assert is_futures("FUT/ES")
    assert not is_crypto("FUT/ES")
    assert not is_equity("FUT/ES")

def test_universe_tiers():
    # etf tier should return exactly ETF_SYMBOLS
    etf = u.get_universe("etf")
    assert set(etf) == set(ETF_SYMBOLS)

    # crypto tier returns crypto list
    crypto = u.get_universe("crypto")
    assert "BTC/USD" in crypto
    assert len(crypto) >= 10

def test_is_crypto_symbol_helper():
    assert u.is_crypto_symbol("BTC/USD")
    assert u.is_crypto_symbol("ETH/USD")
    assert not u.is_crypto_symbol("AAPL")

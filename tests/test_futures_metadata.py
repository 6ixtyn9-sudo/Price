import sys
import types
from pathlib import Path

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from price.futures_metadata import (  # noqa: E402
    canonicalize_futures_symbol,
    get_futures_contract,
    get_research_futures_symbols,
    is_canonical_futures_symbol,
    is_known_futures_symbol,
    provider_symbol_for,
)


def test_canonicalize_futures_symbol_handles_bare_and_canonical():
    assert canonicalize_futures_symbol("ES") == "FUT/ES"
    assert canonicalize_futures_symbol("fut/es") == "FUT/ES"


def test_provider_symbol_for_canonical_futures():
    assert provider_symbol_for("FUT/CL") == "CL"
    contract = get_futures_contract("FUT/NQ")
    assert contract.provider_symbol == "NQ"
    assert contract.execution_ready is False


def test_known_futures_symbol_registry():
    symbols = get_research_futures_symbols()
    assert "FUT/ES" in symbols
    assert is_canonical_futures_symbol("FUT/ES")
    assert is_known_futures_symbol("ES")
    assert not is_known_futures_symbol("SPY")

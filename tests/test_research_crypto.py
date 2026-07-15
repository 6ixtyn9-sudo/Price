import json
import sys
import types
from pathlib import Path

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "scripts", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import research_crypto


def test_load_crypto_symbols_prefers_explicit_crypto_list(tmp_path: Path):
    payload = {
        "crypto": ["btc/usd", "ETH/USD", "ETH/USD"],
        "all": ["SPY", "BTC/USD", "SOL/USD"],
    }
    path = tmp_path / "explicit_allowlist.json"
    path.write_text(json.dumps(payload))

    symbols = research_crypto.load_crypto_symbols(path)

    assert symbols == ["BTC/USD", "ETH/USD"]


def test_load_crypto_symbols_filters_all_when_crypto_missing(tmp_path: Path):
    payload = {"all": ["SPY", "btc/usd", "SOL/USD", "TLT"]}
    path = tmp_path / "explicit_allowlist.json"
    path.write_text(json.dumps(payload))

    symbols = research_crypto.load_crypto_symbols(path)

    assert symbols == ["BTC/USD", "SOL/USD"]


def test_build_discovery_batches_avoids_self_conditioning():
    batches = research_crypto.build_discovery_batches(
        ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
        ["BTC/USD", "ETH/USD"],
    )

    assert batches == [
        {
            "label": "alts",
            "symbols": ["SOL/USD", "DOGE/USD"],
            "condition_symbols": ["BTC/USD", "ETH/USD"],
        },
        {
            "label": "BTC-USD",
            "symbols": ["BTC/USD"],
            "condition_symbols": ["ETH/USD"],
        },
        {
            "label": "ETH-USD",
            "symbols": ["ETH/USD"],
            "condition_symbols": ["BTC/USD"],
        },
    ]


def test_build_discovery_batches_handles_single_condition_symbol():
    batches = research_crypto.build_discovery_batches(
        ["BTC/USD", "SOL/USD"],
        ["BTC/USD"],
    )

    assert batches == [
        {
            "label": "alts",
            "symbols": ["SOL/USD"],
            "condition_symbols": ["BTC/USD"],
        },
        {
            "label": "BTC-USD",
            "symbols": ["BTC/USD"],
            "condition_symbols": [],
        },
    ]

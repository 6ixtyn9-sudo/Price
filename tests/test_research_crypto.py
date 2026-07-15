import json
import sys
import types
from pathlib import Path

import pandas as pd

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


def test_classify_regime_candidate_status_bull_candidate():
    row = pd.Series(
        {
            "strict_gate_pass": False,
            "regime": "bull",
            "slice_n": 22,
            "slice_pass": True,
            "excess_vs_baseline": 0.02,
            "excess_vs_best_parent": 0.01,
            "regime_excess_vs_all": 0.01,
            "search_wide_bh_pass_regime": True,
            "slice_mean_ret_costadj": 0.03,
        }
    )
    assert research_crypto._classify_regime_candidate_status(row) == "bull_regime_candidate"


def test_build_regime_outputs_writes_regime_registry(tmp_path: Path):
    leaderboard = pd.DataFrame(
        [
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "state_ext=neutral + state_slope=downtrend",
                "side": "long",
                "valid_mean_ret_costadj": 0.01,
                "valid_p_value_nw": 0.04,
                "walk_forward_pass_pattern": "0001",
                "search_wide_bh_pass": False,
                "search_wide_bonferroni_pass": False,
            }
        ]
    )
    registry = pd.DataFrame(
        [
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "state_ext=neutral + state_slope=downtrend",
                "candidate_key": "BTC/USD|1d|state_ext=neutral + state_slope=downtrend|rolling",
                "strict_gate_pass": False,
                "status": "research_only",
                "live_decay_flag": False,
            }
        ]
    )
    regime_diagnostics = pd.DataFrame(
        [
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "state_ext=neutral + state_slope=downtrend",
                "regime": "all",
                "diagnostic_status": "ok",
                "slice_n": 30,
                "slice_mean_ret_costadj": 0.01,
                "slice_p_value_nw": 0.04,
                "slice_pass": True,
                "excess_vs_baseline": 0.01,
                "excess_vs_best_parent": 0.005,
            },
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "state_ext=neutral + state_slope=downtrend",
                "regime": "bull",
                "diagnostic_status": "ok",
                "slice_n": 20,
                "slice_mean_ret_costadj": 0.03,
                "slice_p_value_nw": 0.01,
                "slice_pass": True,
                "excess_vs_baseline": 0.02,
                "excess_vs_best_parent": 0.01,
            },
        ]
    )

    regime_registry, summary = research_crypto.build_regime_outputs(
        leaderboard,
        registry,
        regime_diagnostics,
        output_dir=tmp_path,
    )

    assert not regime_registry.empty
    assert (tmp_path / "candidate_registry_crypto_regime.csv").exists()
    assert (tmp_path / "candidate_leaderboard_crypto_bull.csv").exists()
    assert regime_registry.iloc[0]["overall_regime_status"] == "bull_regime_candidate"
    assert summary["regime_candidate_count"] == 1

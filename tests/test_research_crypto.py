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


def test_select_regime_targets_caps_total_and_per_symbol():
    leaderboard = pd.DataFrame(
        [
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": f"btc_{i}",
                "side": "long",
                "triage_bucket": "clean_survivor_wf_mixed",
                "search_wide_bh_pass": True,
                "search_wide_bonferroni_pass": False,
                "robustness_score": 100 - i,
                "valid_mean_ret_costadj": 0.01,
                "walk_forward_survival_rate": 0.5,
            }
            for i in range(4)
        ]
        + [
            {
                "symbol": "ETH/USD",
                "timeframe": "1d",
                "slice_combination": f"eth_{i}",
                "side": "short",
                "triage_bucket": "late_emerging_recent_only",
                "search_wide_bh_pass": False,
                "search_wide_bonferroni_pass": False,
                "robustness_score": 50 - i,
                "valid_mean_ret_costadj": 0.02,
                "walk_forward_survival_rate": 0.25,
            }
            for i in range(4)
        ]
    )

    selected, targets = research_crypto._select_regime_targets(
        leaderboard,
        max_targets=3,
        max_per_symbol=2,
    )

    assert len(selected) == 3
    assert len(targets) == 3
    assert (selected["symbol"] == "BTC/USD").sum() <= 2


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


def test_classify_regime_candidate_status_not_evaluated():
    row = pd.Series({"not_regime_evaluated": True})
    assert research_crypto._classify_regime_candidate_status(row) == "not_regime_evaluated"


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
    assert summary["regime_target_count"] == 1


def test_build_regime_outputs_marks_non_selected_as_not_regime_evaluated(tmp_path: Path):
    leaderboard = pd.DataFrame(
        [
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "slice_a",
                "side": "long",
                "valid_mean_ret_costadj": 0.01,
                "valid_p_value_nw": 0.04,
                "walk_forward_pass_pattern": "0001",
                "search_wide_bh_pass": False,
                "search_wide_bonferroni_pass": False,
            },
            {
                "symbol": "ETH/USD",
                "timeframe": "1d",
                "slice_combination": "slice_b",
                "side": "short",
                "valid_mean_ret_costadj": 0.02,
                "valid_p_value_nw": 0.03,
                "walk_forward_pass_pattern": "0001",
                "search_wide_bh_pass": False,
                "search_wide_bonferroni_pass": False,
            },
        ]
    )
    registry = pd.DataFrame(
        [
            {"symbol": "BTC/USD", "timeframe": "1d", "slice_combination": "slice_a", "strict_gate_pass": False, "status": "research_only", "live_decay_flag": False},
            {"symbol": "ETH/USD", "timeframe": "1d", "slice_combination": "slice_b", "strict_gate_pass": False, "status": "research_only", "live_decay_flag": False},
        ]
    )
    regime_diagnostics = pd.DataFrame(
        [
            {"symbol": "BTC/USD", "timeframe": "1d", "slice_combination": "slice_a", "regime": "all", "diagnostic_status": "ok", "slice_n": 20, "slice_mean_ret_costadj": 0.01, "slice_p_value_nw": 0.04, "slice_pass": True, "excess_vs_baseline": 0.01, "excess_vs_best_parent": 0.01},
            {"symbol": "BTC/USD", "timeframe": "1d", "slice_combination": "slice_a", "regime": "bull", "diagnostic_status": "ok", "slice_n": 20, "slice_mean_ret_costadj": 0.03, "slice_p_value_nw": 0.01, "slice_pass": True, "excess_vs_baseline": 0.02, "excess_vs_best_parent": 0.01},
        ]
    )
    selected_targets_df = leaderboard.iloc[[0]].copy()

    regime_registry, summary = research_crypto.build_regime_outputs(
        leaderboard,
        registry,
        regime_diagnostics,
        output_dir=tmp_path,
        selected_targets_df=selected_targets_df,
    )

    status_map = dict(zip(regime_registry["slice_combination"], regime_registry["overall_regime_status"]))
    assert status_map["slice_a"] == "bull_regime_candidate"
    assert status_map["slice_b"] == "not_regime_evaluated"
    assert summary["regime_not_evaluated_count"] == 1


def test_build_monitored_candidates_selects_regime_candidates(tmp_path: Path):
    regime_registry = pd.DataFrame(
        [
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "slice_a",
                "side": "short",
                "overall_regime_status": "bull_regime_candidate",
                "best_regime": "bull",
                "best_regime_mean_ret_costadj": 0.05,
                "best_regime_p_value_nw": 0.001,
            },
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "slice_b",
                "side": "long",
                "overall_regime_status": "unsupported",
                "best_regime": "",
                "best_regime_mean_ret_costadj": None,
                "best_regime_p_value_nw": None,
            },
        ]
    )
    leaderboard = pd.DataFrame(
        [
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "slice_a",
                "triage_bucket": "clean_survivor_wf_mixed",
                "valid_n": 25,
                "valid_mean_ret_costadj": 0.03,
                "valid_p_value_nw": 0.01,
                "walk_forward_pass_pattern": "0011",
                "search_wide_bh_pass": True,
                "search_wide_bonferroni_pass": False,
                "bin_mode": "rolling",
            },
            {
                "symbol": "BTC/USD",
                "timeframe": "1d",
                "slice_combination": "slice_b",
                "triage_bucket": "rejected_unsupported",
                "valid_n": 25,
                "valid_mean_ret_costadj": 0.00,
                "valid_p_value_nw": 0.9,
                "walk_forward_pass_pattern": "0000",
                "search_wide_bh_pass": False,
                "search_wide_bonferroni_pass": False,
                "bin_mode": "rolling",
            },
        ]
    )

    monitored, summary = research_crypto.build_monitored_candidates(
        regime_registry,
        leaderboard,
        output_dir=tmp_path,
        max_candidates=10,
        max_per_symbol=2,
    )

    assert len(monitored) == 1
    assert monitored.iloc[0]["slice_combination"] == "slice_a"
    assert (tmp_path / "monitored_candidates_crypto.csv").exists()
    assert summary["monitored_candidate_count"] == 1


def test_run_crypto_research_regime_only_reuses_existing_artifacts(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "crypto"
    output_dir.mkdir(parents=True)
    pd.DataFrame(
        [{"symbol": "BTC/USD", "timeframe": "1d", "slice_combination": "slice_a", "side": "long", "triage_bucket": "clean_survivor_wf_mixed", "search_wide_bh_pass": True, "search_wide_bonferroni_pass": False, "robustness_score": 1.0, "valid_mean_ret_costadj": 0.01, "walk_forward_survival_rate": 0.5}]
    ).to_csv(output_dir / "candidate_leaderboard_crypto_rolling.csv", index=False)
    pd.DataFrame(
        [{"symbol": "BTC/USD", "timeframe": "1d", "slice_combination": "slice_a"}]
    ).to_csv(output_dir / "discovered_slices_crypto_rolling.csv", index=False)
    pd.DataFrame(
        [{"symbol": "BTC/USD", "timeframe": "1d", "slice_combination": "slice_a", "strict_gate_pass": False, "status": "research_only", "live_decay_flag": False}]
    ).to_csv(output_dir / "candidate_registry_crypto_rolling.csv", index=False)

    monkeypatch.setattr(research_crypto.validate_slices, "run_date_range_diagnostics", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(
        research_crypto.validate_slices,
        "run_regime_stratified_diagnostics",
        lambda **kwargs: pd.DataFrame(
            [{"symbol": "BTC/USD", "timeframe": "1d", "slice_combination": "slice_a", "regime": "all", "diagnostic_status": "ok", "slice_n": 20, "slice_mean_ret_costadj": 0.01, "slice_p_value_nw": 0.04, "slice_pass": True, "excess_vs_baseline": 0.01, "excess_vs_best_parent": 0.01}]
        ),
    )
    monkeypatch.setattr(research_crypto, "build_regime_outputs", lambda *a, **k: (pd.DataFrame(), {"regime_status_counts": {}, "regime_leaderboard_rows": {"bull": 0, "bear": 0, "neutral": 0}, "regime_candidate_count": 0, "top_regime_candidates": []}))

    result = research_crypto.run_crypto_research(
        symbols=["BTC/USD"],
        timeframes=("1d",),
        output_dir=output_dir,
        regime_only=True,
    )

    assert result["symbol_count"] == 1
    assert result["leaderboard_rows"] == 1

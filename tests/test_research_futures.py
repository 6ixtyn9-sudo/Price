import sys
import types
from pathlib import Path

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "scripts", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pandas as pd

import research_futures


def test_futures_defaults_are_research_only():
    assert research_futures.DEFAULT_BIN_MODE == "rolling"
    assert research_futures.DEFAULT_TIMEFRAMES == ("1d",)
    assert research_futures.DEFAULT_OUTPUT_DIR.as_posix().endswith("localdata/research/futures")


def test_build_regime_outputs_writes_futures_regime_registry(tmp_path: Path):
    leaderboard = pd.DataFrame(
        [
            {
                "symbol": "FUT/CL",
                "timeframe": "1d",
                "slice_combination": "state_ext=stretched_down + state_vol=high_vol",
                "side": "short",
                "valid_mean_ret_costadj": 0.07,
                "valid_p_value_nw": 0.001,
                "walk_forward_pass_pattern": "0001",
                "search_wide_bh_pass": True,
                "search_wide_bonferroni_pass": True,
            }
        ]
    )
    registry = pd.DataFrame(
        [
            {
                "symbol": "FUT/CL",
                "timeframe": "1d",
                "slice_combination": "state_ext=stretched_down + state_vol=high_vol",
                "strict_gate_pass": False,
                "status": "research_only",
                "live_decay_flag": False,
            }
        ]
    )
    regime_diagnostics = pd.DataFrame(
        [
            {
                "symbol": "FUT/CL",
                "timeframe": "1d",
                "slice_combination": "state_ext=stretched_down + state_vol=high_vol",
                "regime": "all",
                "diagnostic_status": "ok",
                "slice_n": 15,
                "slice_mean_ret_costadj": 0.07,
                "slice_p_value_nw": 0.001,
                "slice_pass": True,
                "excess_vs_baseline": 0.06,
                "excess_vs_best_parent": 0.001,
            },
            {
                "symbol": "FUT/CL",
                "timeframe": "1d",
                "slice_combination": "state_ext=stretched_down + state_vol=high_vol",
                "regime": "neutral",
                "diagnostic_status": "ok",
                "slice_n": 15,
                "slice_mean_ret_costadj": 0.08,
                "slice_p_value_nw": 0.001,
                "slice_pass": True,
                "excess_vs_baseline": 0.04,
                "excess_vs_best_parent": 0.001,
            },
        ]
    )

    regime_registry, summary = research_futures.build_regime_outputs(
        leaderboard,
        registry,
        regime_diagnostics,
        output_dir=tmp_path,
    )

    assert not regime_registry.empty
    assert (tmp_path / "candidate_registry_futures_regime.csv").exists()
    assert (tmp_path / "candidate_leaderboard_futures_neutral.csv").exists()
    assert summary["regime_candidate_count"] == 1


def test_load_existing_futures_artifacts_accepts_merged_filenames(tmp_path: Path):
    (tmp_path / "discovered_slices_merged.csv").write_text("symbol,timeframe,slice_combination\nFUT/ES,1d,a\n")
    (tmp_path / "candidate_leaderboard_merged.csv").write_text("symbol,timeframe,slice_combination\nFUT/ES,1d,a\n")
    (tmp_path / "candidate_registry.csv").write_text("symbol,timeframe,slice_combination\nFUT/ES,1d,a\n")

    discovered, leaderboard, registry = research_futures._load_existing_futures_artifacts(tmp_path)

    assert len(discovered) == 1
    assert len(leaderboard) == 1
    assert len(registry) == 1

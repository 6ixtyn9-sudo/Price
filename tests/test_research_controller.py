"""Tests for the autonomous research controller's pure lifecycle gates."""

import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research_lifecycle import apply_registry_to_monitored, build_registry  # noqa: E402
from research_refresh import _new_daily_bars  # noqa: E402
from research_regime_coverage import _regime_series  # noqa: E402


def test_first_refresh_baseline_does_not_count_history_as_new():
    current = {"SPY": {"count": 1254}, "QQQ": {"count": 1254}}
    assert _new_daily_bars({}, current) == 2508


def test_regime_coverage_labels_bull_bear_and_warmup():
    close = [100.0] * 250
    close[-60:] = [100.0 + i for i in range(60)]
    df = pd.DataFrame({"close_adj": close})
    regimes = _regime_series(df)
    assert "warmup" in set(regimes)
    assert regimes.iloc[-1] in {"bull", "neutral"}


def test_lifecycle_registry_requires_strict_gate_and_flags_decay(tmp_path):
    leaderboard = tmp_path / "leaderboard.csv"
    live = tmp_path / "live.csv"
    output = tmp_path / "registry.csv"
    combo = "state_ext=stretched_down + state_slope=downtrend"
    pd.DataFrame([
        {
            "symbol": "XOP", "timeframe": "1d", "slice_combination": combo,
            "side": "long", "bin_mode": "rolling", "triage_bucket": "clean_survivor_wf_strong",
            "valid_n": 40, "walk_forward_pass_count": 3,
            "scenario_survived_count": 5, "search_wide_bh_pass": True,
            "valid_excess_vs_baseline": 0.01, "valid_excess_vs_best_parent": 0.002,
        },
        {
            "symbol": "BAD", "timeframe": "1d", "slice_combination": combo,
            "side": "long", "bin_mode": "rolling", "triage_bucket": "rejected_unsupported",
            "valid_n": 40, "walk_forward_pass_count": 4,
            "scenario_survived_count": 8, "search_wide_bh_pass": True,
            "valid_excess_vs_baseline": 0.01, "valid_excess_vs_best_parent": 0.002,
        },
    ]).to_csv(leaderboard, index=False)
    pd.DataFrame([
        {
            "symbol": "XOP", "timeframe": "1d", "slice_combination": combo,
            "bin_mode": "rolling", "fwd_ret_5b": -0.01,
        }
    ] * 5).to_csv(live, index=False)

    registry = build_registry(leaderboard, output_path=output, live_forward_path=live)
    xop = registry[registry["symbol"] == "XOP"].iloc[0]
    bad = registry[registry["symbol"] == "BAD"].iloc[0]
    assert xop["status"] == "decaying_suspended"
    assert bad["status"] == "research_only"
    assert output.exists()


def test_auto_apply_demotes_only_explicit_decay_and_adds_approved(tmp_path):
    monitored = tmp_path / "monitored_slices.csv"
    pd.DataFrame([
        {"symbol": "OLD", "timeframe": "1d", "slice_combination": "old", "side": "long", "bin_mode": "insample"},
        {"symbol": "KEEP", "timeframe": "1d", "slice_combination": "keep", "side": "long", "bin_mode": "insample"},
    ]).to_csv(monitored, index=False)
    registry = pd.DataFrame([
        {"symbol": "OLD", "timeframe": "1d", "slice_combination": "old", "side": "long", "bin_mode": "insample", "candidate_key": "OLD|1d|old|insample", "status": "decaying_suspended"},
        {"symbol": "NEW", "timeframe": "1d", "slice_combination": "new", "side": "long", "bin_mode": "rolling", "candidate_key": "NEW|1d|new|rolling", "status": "auto_approved"},
    ])
    result = apply_registry_to_monitored(registry, monitored)
    keys = set(zip(result["symbol"], result["slice_combination"]))
    assert ("OLD", "old") not in keys
    assert ("KEEP", "keep") in keys
    assert ("NEW", "new") in keys

"""Tests for the autonomous research controller's pure lifecycle gates."""

import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research_lifecycle import (  # noqa: E402
    apply_registry_to_monitored,
    build_registry,
    normalize_walk_forward_patterns,
)
from research_refresh import (  # noqa: E402
    _eligible_discovery_symbols,
    _monitored_diagnostic_bin_mode,
    _new_daily_bars,
)
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


def test_discovery_gate_is_per_symbol_not_aggregate():
    previous = {"SPY": {"count": 1254}, "QQQ": {"count": 1254}, "IWM": {"count": 1254}}
    current = {"SPY": {"count": 1314}, "QQQ": {"count": 1255}, "IWM": {"count": 1254}}
    assert _eligible_discovery_symbols(previous, current, 60) == ["SPY"]


def test_research_refresh_separates_fresh_gate_from_sharded_discovery(monkeypatch, tmp_path):
    import research_refresh as rr

    monkeypatch.chdir(tmp_path)
    previous = {f"S{i}": {"count": 100} for i in range(60)}
    current = {f"S{i}": {"count": 160, "last_bar": "2026-07-10"} for i in range(60)}
    monkeypatch.setattr(rr, "_load_state", lambda: {"daily_coverage": previous})
    monkeypatch.setattr(rr, "_coverage", lambda symbols, *args, **kwargs: current)
    monkeypatch.setattr(rr, "build_coverage", lambda symbols, tfs: pd.DataFrame())
    monkeypatch.setattr(rr, "build_regime_opportunity_rates", lambda: pd.DataFrame())
    written = {}
    monkeypatch.setattr(rr, "_write_state", lambda payload: written.update(payload))

    state = rr.run_refresh(
        symbols=list(current),
        timeframes=("1d", "1h", "15m"),
        allow_discovery=True,
        min_new_daily_bars=60,
    )

    assert state["fresh_data_gate_open"] is True
    assert state["sharded_discovery_required"] is True
    assert state["unsharded_discovery_allowed"] is False
    assert state["discovery_allowed"] is False
    assert state["discovery_ran"] is False
    assert written["sharded_discovery_required"] is True


def test_diagnostic_bin_mode_follows_monitored_book(tmp_path):
    monitored = tmp_path / "monitored_slices.csv"
    pd.DataFrame([{"symbol": "XLF", "bin_mode": "rolling"}]).to_csv(monitored, index=False)
    assert _monitored_diagnostic_bin_mode(monitored) == "rolling"

    pd.DataFrame([
        {"symbol": "XLF", "bin_mode": "insample"},
        {"symbol": "XOP", "bin_mode": "rolling"},
    ]).to_csv(monitored, index=False)
    assert _monitored_diagnostic_bin_mode(monitored) == "insample"


def test_walk_forward_patterns_preserve_fold_width():
    frame = pd.DataFrame({
        "validation_n_folds": [4, 4, 4, 4],
        "walk_forward_pass_pattern": [1, 10, 101, 111],
    })
    fixed = normalize_walk_forward_patterns(frame)
    assert fixed["walk_forward_pass_pattern"].tolist() == ["0001", "0010", "0101", "0111"]

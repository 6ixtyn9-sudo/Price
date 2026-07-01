import sys
from pathlib import Path
import pandas as pd

# scripts/ is not a package; import it by adding the scripts dir to sys.path,
# consistent with how validate_slices.py is invoked directly (python scripts/validate_slices.py).
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_slices import (  # noqa: E402
    classify_verdict,
    evidence_supports,
    run_scenario_grid,
    run_walk_forward_diagnostics,
    summarize_baseline_train_valid,
    summarize_parent_baselines_train_valid,
    survives,
)

P_THRESHOLD = 0.05


def _summary(sample_count, mean_return, p_value, meets_min_samples):
    return {
        "sample_count": sample_count,
        "mean_return": mean_return,
        "p_value": p_value,
        "meets_min_samples": meets_min_samples,
    }


def test_survives_requires_min_samples_floor():
    strong_but_starved = _summary(sample_count=13, mean_return=0.01, p_value=0.01, meets_min_samples=False)
    assert survives(strong_but_starved, min_samples=15, p_threshold=P_THRESHOLD) is False


def test_survives_true_when_all_conditions_met():
    strong_and_sufficient = _summary(sample_count=20, mean_return=0.01, p_value=0.01, meets_min_samples=True)
    assert survives(strong_and_sufficient, min_samples=15, p_threshold=P_THRESHOLD) is True


def test_evidence_supports_ignores_sample_floor():
    starved_but_significant = _summary(sample_count=13, mean_return=0.01, p_value=0.01, meets_min_samples=False)
    assert evidence_supports(starved_but_significant, p_threshold=P_THRESHOLD) is True


def test_evidence_supports_rejects_wrong_sign():
    negative_edge = _summary(sample_count=100, mean_return=-0.01, p_value=0.01, meets_min_samples=True)
    assert evidence_supports(negative_edge, p_threshold=P_THRESHOLD) is False


def test_evidence_supports_rejects_insignificant():
    insignificant = _summary(sample_count=100, mean_return=0.01, p_value=0.5, meets_min_samples=True)
    assert evidence_supports(insignificant, p_threshold=P_THRESHOLD) is False


def test_classify_verdict_survived_when_both_pass():
    train = _summary(sample_count=100, mean_return=0.01, p_value=0.01, meets_min_samples=True)
    valid = _summary(sample_count=50, mean_return=0.01, p_value=0.01, meets_min_samples=True)
    verdict = classify_verdict(
        train_pass=True, valid_pass=True, train_summary=train, valid_summary=valid, p_threshold=P_THRESHOLD
    )
    assert verdict == "survived"


def test_classify_verdict_provisional_when_starved_but_directionally_supported():
    # Mirrors the real QQQ afternoon-reversal case: train_n=13 (below floor)
    # but positive+significant; valid_n=18 (above floor), positive+significant.
    train = _summary(sample_count=13, mean_return=0.00996, p_value=0.03, meets_min_samples=False)
    valid = _summary(sample_count=18, mean_return=0.00613, p_value=0.044, meets_min_samples=True)
    train_pass = survives(train, min_samples=15, p_threshold=P_THRESHOLD)
    valid_pass = survives(valid, min_samples=15, p_threshold=P_THRESHOLD)

    assert train_pass is False
    assert valid_pass is True

    verdict = classify_verdict(train_pass, valid_pass, train, valid, P_THRESHOLD)
    assert verdict == "provisional"


def test_classify_verdict_rejected_when_evidence_does_not_support_edge():
    # Enough samples, but not significant -> genuinely rejected, not provisional.
    train = _summary(sample_count=100, mean_return=0.001, p_value=0.4, meets_min_samples=True)
    valid = _summary(sample_count=50, mean_return=-0.002, p_value=0.6, meets_min_samples=True)
    train_pass = survives(train, min_samples=15, p_threshold=P_THRESHOLD)
    valid_pass = survives(valid, min_samples=15, p_threshold=P_THRESHOLD)

    verdict = classify_verdict(train_pass, valid_pass, train, valid, P_THRESHOLD)
    assert verdict == "rejected"


def test_classify_verdict_rejected_when_starved_and_unsupported():
    # Starved AND wrong sign/insignificant -> still rejected, not provisional.
    train = _summary(sample_count=13, mean_return=-0.005, p_value=0.3, meets_min_samples=False)
    valid = _summary(sample_count=50, mean_return=0.001, p_value=0.4, meets_min_samples=True)
    train_pass = survives(train, min_samples=15, p_threshold=P_THRESHOLD)
    valid_pass = survives(valid, min_samples=15, p_threshold=P_THRESHOLD)

    verdict = classify_verdict(train_pass, valid_pass, train, valid, P_THRESHOLD)
    assert verdict == "rejected"

def test_summarize_baseline_train_valid_uses_same_chronological_split():
    df = pd.DataFrame(
        {
            "bar_ts_utc": pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC"),
            "fwd_ret_5": [0.01] * 7 + [0.02] * 3,
            "close_adj": [100.0] * 10,
        }
    )

    baseline = summarize_baseline_train_valid(
        df,
        split=0.7,
        cost_bps=0.0,
        min_samples=1,
    )

    assert baseline["train"]["sample_count"] == 7
    assert baseline["valid"]["sample_count"] == 3
    assert baseline["train"]["mean_return"] == 0.01
    assert baseline["valid"]["mean_return"] == 0.02

def test_summarize_parent_baselines_train_valid_finds_strongest_parent():
    df = pd.DataFrame(
        {
            "bar_ts_utc": pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC"),
            "fwd_ret_5": [0.01] * 7 + [0.02, 0.04, 0.06],
            "close_adj": [100.0] * 10,
            "state_a": ["x"] * 10,
            "state_b": ["n"] * 6 + ["y", "n", "n", "y"],
        }
    )

    parent = summarize_parent_baselines_train_valid(
        df,
        {"state_a": "x", "state_b": "y"},
        split=0.7,
        cost_bps=0.0,
        min_samples=1,
    )

    assert parent["valid"]["filter"] == "state_b=y"
    assert parent["valid"]["sample_count"] == 1
    assert parent["valid"]["mean_return"] == 0.06

def test_run_scenario_grid_collects_target_rows(monkeypatch, tmp_path):
    def fake_run_validation(**kwargs):
        cost_bps = kwargs.get("cost_bps", 1.0)
        split = kwargs.get("split", 0.7)
        if cost_bps == 2.0:
            label_ret = 0.002
        elif cost_bps == 5.0:
            label_ret = 0.005
        elif split == 0.6:
            label_ret = 0.006
        elif split == 0.8:
            label_ret = 0.008
        else:
            label_ret = 0.001

        return pd.DataFrame(
            [
                {
                    "symbol": "SPY",
                    "timeframe": "1h",
                    "slice_combination": "state_session=afternoon + state_slope=downtrend",
                    "train_n": 100,
                    "valid_n": 50,
                    "valid_mean_ret_costadj": label_ret,
                    "valid_baseline_mean_ret_costadj": 0.0,
                    "valid_excess_vs_baseline": label_ret,
                    "valid_best_parent_filter": "state_slope=downtrend",
                    "valid_best_parent_mean_ret_costadj": 0.0,
                    "valid_excess_vs_best_parent": label_ret,
                    "valid_p_value_nw": 0.01,
                    "walk_forward_survival_rate": 0.75,
                    "verdict": "survived",
                }
            ]
        )

    monkeypatch.setattr("validate_slices.run_validation", fake_run_validation)

    output_path = tmp_path / "scenario_grid.csv"
    result = run_scenario_grid(output_path=str(output_path))

    assert output_path.exists()
    assert set(result["scenario"]) == {"default", "cost2", "cost5", "split06", "split08"}
    assert len(result) == 15
    assert (
        result[
            (result["scenario"] == "default")
            & (result["symbol"] == "SPY")
            & (result["slice_combination"] == "state_session=afternoon + state_slope=downtrend")
        ]["valid_mean_ret_costadj"].iloc[0]
        == 0.001
    )

def test_run_walk_forward_diagnostics_writes_fold_rows(monkeypatch, tmp_path):
    def fake_build_eligible_frame(symbol, timeframe):
        return pd.DataFrame(
            {
                "bar_ts_utc": pd.date_range("2024-01-01", periods=30, freq="h", tz="UTC"),
                "fwd_ret_5": [0.01] * 30,
                "close_adj": [100.0] * 30,
                "state_session": ["afternoon", "lunch", "lunch"] * 10,
                "state_slope": ["downtrend"] * 30,
            }
        )

    monkeypatch.setattr("validate_slices.build_eligible_frame", fake_build_eligible_frame)

    output_path = tmp_path / "walk_forward_diagnostics.csv"
    result = run_walk_forward_diagnostics(
        n_folds=2,
        min_samples=1,
        output_path=str(output_path),
    )

    assert output_path.exists()
    assert len(result) == 6
    assert set(result["fold"]) == {0, 1}
    assert set(result["diagnostic_status"]) == {"ok"}
    assert "valid_excess_vs_baseline" in result.columns
    assert "valid_excess_vs_best_parent" in result.columns


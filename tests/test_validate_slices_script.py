import sys
from pathlib import Path
import pandas as pd

# scripts/ is not a package; import it by adding the scripts dir to sys.path,
# consistent with how validate_slices.py is invoked directly (python scripts/validate_slices.py).
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_slices import (  # noqa: E402
    cross_symbols_from_filter,
    annotate_search_wide_significance,
    _filter_date_window,
    classify_candidate_triage,
    classify_verdict,
    evidence_supports,
    select_diagnostic_targets,
    run_candidate_leaderboard,
    run_date_range_diagnostics,
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
    """Parent-excess uses the strongest simpler parent on the full eligible frame.

    This is the intentionally conservative design: a child slice must beat the
    best simpler parent regime in the same chronological window, not just the
    parent restricted to the child's own observation set.

    With 10 bars and a 70/30 split, the validation window is rows 7-9 with
    returns 0.02, 0.04, 0.06. Parent state_a=x holds on all 3 valid rows
    (mean 0.04). Parent state_b=y holds only on row 9 (mean 0.06), so
    state_b=y is the strongest valid parent.
    """
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
                    "train_pass": True,
                    "valid_n": 50,
                    "valid_pass": True,
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
    def fake_build_eligible_frame(symbol, timeframe, cross_symbols=None, bin_mode="insample"):
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

def test_filter_date_window_uses_half_open_utc_window():
    df = pd.DataFrame(
        {
            "bar_ts_utc": pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC"),
            "value": range(5),
        }
    )

    result = _filter_date_window(
        df,
        start=pd.Timestamp("2024-01-01 01:00:00", tz="UTC"),
        end=pd.Timestamp("2024-01-01 04:00:00", tz="UTC"),
    )

    assert result["value"].tolist() == [1, 2, 3]


def test_run_date_range_diagnostics_writes_target_windows(monkeypatch, tmp_path):
    def fake_build_eligible_frame(symbol, timeframe, cross_symbols=None, bin_mode="insample"):
        return pd.DataFrame(
            {
                "bar_ts_utc": pd.date_range("2024-01-01", periods=900, freq="8h", tz="UTC"),
                "fwd_ret_5": [0.01] * 900,
                "close_adj": [100.0] * 900,
                "state_session": ["afternoon", "lunch", "lunch"] * 300,
                "state_slope": ["downtrend"] * 900,
            }
        )

    monkeypatch.setattr("validate_slices.build_eligible_frame", fake_build_eligible_frame)

    output_path = tmp_path / "date_range_diagnostics.csv"
    result = run_date_range_diagnostics(
        min_samples=1,
        output_path=str(output_path),
    )

    assert output_path.exists()
    assert set(result["window"]) == {
        "all",
        "calendar_2024",
        "calendar_2025",
        "calendar_2026_ytd",
        "latest_12m",
        "latest_6m",
    }
    assert len(result) == 18
    assert "excess_vs_baseline" in result.columns
    assert "excess_vs_best_parent" in result.columns


def test_run_date_range_diagnostics_accepts_explicit_targets(monkeypatch, tmp_path):
    calls = []

    def fake_build_eligible_frame(symbol, timeframe, cross_symbols=None, bin_mode="insample"):
        calls.append((symbol, timeframe, tuple(sorted((cross_symbols or {}).keys())), bin_mode))
        return pd.DataFrame(
            {
                "bar_ts_utc": pd.date_range("2024-01-01", periods=300, freq="D", tz="UTC"),
                "fwd_ret_5": [0.01] * 300,
                "close_adj": [100.0] * 300,
                "state_ext": ["neutral"] * 300,
                "state_slope": ["downtrend"] * 300,
            }
        )

    monkeypatch.setattr("validate_slices.build_eligible_frame", fake_build_eligible_frame)

    output_path = tmp_path / "date_range_targets.csv"
    targets = [("BTC/USD", "1d", "state_ext=neutral + state_slope=downtrend", "long")]
    result = run_date_range_diagnostics(
        min_samples=1,
        output_path=str(output_path),
        targets=targets,
    )

    assert output_path.exists()
    assert len(calls) == 1
    assert set(result["symbol"]) == {"BTC/USD"}


def test_run_candidate_leaderboard_ranks_all_rows(monkeypatch, tmp_path):
    def fake_run_validation(**kwargs):
        cost_bps = kwargs.get("cost_bps", 1.0)
        split = kwargs.get("split", 0.7)

        if cost_bps == 5.0:
            spy_verdict = "rejected"
        elif split == 0.8:
            spy_verdict = "rejected"
        else:
            spy_verdict = "survived"

        return pd.DataFrame(
            [
                {
                    "symbol": "SPY",
                    "timeframe": "1h",
                    "slice_combination": "state_session=afternoon + state_slope=downtrend",
                    "train_n": 100,
                    "train_pass": True,
                    "valid_n": 50,
                    "valid_pass": True,
                    "valid_mean_ret_costadj": 0.002,
                    "valid_excess_vs_baseline": 0.001,
                    "valid_best_parent_filter": "state_slope=downtrend",
                    "valid_excess_vs_best_parent": 0.0005,
                    "valid_p_value_nw": 0.01,
                    "walk_forward_pass_count": 3,
                    "walk_forward_pass_pattern": "1110",
                    "walk_forward_survival_rate": 0.75,
                    "verdict": spy_verdict,
                },
                {
                    "symbol": "QQQ",
                    "timeframe": "1h",
                    "slice_combination": "state_session=lunch + state_slope=downtrend",
                    "train_n": 100,
                    "train_pass": True,
                    "valid_n": 50,
                    "valid_pass": True,
                    "valid_mean_ret_costadj": 0.003,
                    "valid_excess_vs_baseline": 0.002,
                    "valid_best_parent_filter": "state_slope=downtrend",
                    "valid_excess_vs_best_parent": 0.001,
                    "valid_p_value_nw": 0.02,
                    "walk_forward_pass_count": 1,
                    "walk_forward_pass_pattern": "0001",
                    "walk_forward_survival_rate": 0.25,
                    "verdict": "survived",
                },
            ]
        )

    monkeypatch.setattr("validate_slices.run_validation", fake_run_validation)

    output_path = tmp_path / "candidate_leaderboard.csv"
    result = run_candidate_leaderboard(output_path=str(output_path))

    assert output_path.exists()
    assert len(result) == 2
    assert result["rank"].tolist() == [1, 2]
    assert "scenario_survived_count" in result.columns
    assert "robustness_score" in result.columns
    assert set(result["symbol"]) == {"SPY", "QQQ"}

def test_classify_candidate_triage_clean_survivor_without_fold_pattern():
    bucket = classify_candidate_triage(
        verdict="survived",
        train_pass=True,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
    )
    assert bucket == "clean_survivor"


def test_classify_candidate_triage_over_specified_survivor():
    bucket = classify_candidate_triage(
        verdict="survived",
        train_pass=True,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=-0.0001,
        valid_p_value_nw=0.01,
    )
    assert bucket == "over_specified_survivor"


def test_classify_candidate_triage_late_emerging_valid_supported():
    bucket = classify_candidate_triage(
        verdict="rejected",
        train_pass=False,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
    )
    assert bucket == "late_emerging_valid_supported"


def test_classify_candidate_triage_provisional_sample_starved():
    bucket = classify_candidate_triage(
        verdict="provisional",
        train_pass=True,
        valid_pass=False,
        valid_n=6,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
    )
    assert bucket == "provisional_sample_starved"

def test_select_diagnostic_targets_late_emerging_from_leaderboard(monkeypatch):
    fake_leaderboard = pd.DataFrame(
        [
            {
                "symbol": "SPY",
                "timeframe": "1h",
                "slice_combination": "clean",
                "triage_bucket": "clean_survivor",
            },
            {
                "symbol": "QQQ",
                "timeframe": "1h",
                "slice_combination": "late_one",
                "triage_bucket": "late_emerging_valid_supported",
            },
            {
                "symbol": "QQQ",
                "timeframe": "1d",
                "slice_combination": "late_two",
                "triage_bucket": "late_emerging_valid_supported",
            },
        ]
    )

    monkeypatch.setattr("validate_slices.run_candidate_leaderboard", lambda **kwargs: fake_leaderboard)

    targets = select_diagnostic_targets(scope="late-emerging", top_n=1)

    assert targets == [("QQQ", "1h", "late_one", "long")]


def test_select_diagnostic_targets_rejects_unknown_scope():
    try:
        select_diagnostic_targets(scope="unknown")
    except ValueError as exc:
        assert "diagnostic scope must be one of" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown diagnostic scope")

def test_classify_candidate_triage_late_emerging_recent_only():
    bucket = classify_candidate_triage(
        verdict="rejected",
        train_pass=False,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
        walk_forward_pass_pattern="0001",
    )
    assert bucket == "late_emerging_recent_only"


def test_classify_candidate_triage_late_emerging_regime_switching():
    bucket = classify_candidate_triage(
        verdict="rejected",
        train_pass=False,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
        walk_forward_pass_pattern="1001",
    )
    assert bucket == "late_emerging_regime_switching"

def test_classify_candidate_triage_clean_survivor_wf_strong():
    bucket = classify_candidate_triage(
        verdict="survived",
        train_pass=True,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
        walk_forward_pass_pattern="1111",
    )
    assert bucket == "clean_survivor_wf_strong"


def test_classify_candidate_triage_clean_survivor_wf_mixed():
    bucket = classify_candidate_triage(
        verdict="survived",
        train_pass=True,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
        walk_forward_pass_pattern="1001",
    )
    assert bucket == "clean_survivor_wf_mixed"


def test_classify_candidate_triage_clean_survivor_wf_failed():
    bucket = classify_candidate_triage(
        verdict="survived",
        train_pass=True,
        valid_pass=True,
        valid_n=100,
        valid_excess_vs_baseline=0.001,
        valid_excess_vs_best_parent=0.0005,
        valid_p_value_nw=0.01,
        walk_forward_pass_pattern="0000",
    )
    assert bucket == "clean_survivor_wf_failed"


def test_annotate_search_wide_significance_bh_and_bonferroni():
    # Family of 10 finite p-values plus one NaN (excluded from the family).
    pvals = [
        0.0000001,  # tiny -> clears Bonferroni and BH
        0.008,      # clears BH (crit 0.01) but not Bonferroni (0.005)
        0.02,
        0.04,
        0.20,
        0.30,
        0.40,
        0.50,
        0.60,
        0.90,
    ]
    lb = pd.DataFrame(
        {
            "symbol": [f"S{i}" for i in range(10)],
            "valid_p_value_nw": pvals,
        }
    )
    lb = pd.concat(
        [lb, pd.DataFrame({"symbol": ["NAN"], "valid_p_value_nw": [float("nan")]})],
        ignore_index=True,
    )

    out = annotate_search_wide_significance(lb, p_threshold=0.05)

    # Family size counts only the 10 finite p-values.
    assert (out["search_wide_family_size"] == 10).all()

    # The NaN row never passes and has no rank.
    nan_row = out[out["symbol"] == "NAN"].iloc[0]
    assert nan_row["search_wide_bh_pass"] == False  # noqa: E712
    assert nan_row["search_wide_bonferroni_pass"] == False  # noqa: E712
    assert pd.isna(nan_row["search_wide_rank"])

    # Smallest p clears Bonferroni (0.05/10 = 0.005) and BH.
    top = out[out["symbol"] == "S0"].iloc[0]
    assert top["search_wide_rank"] == 1
    assert top["search_wide_bonferroni_pass"] == True  # noqa: E712
    assert top["search_wide_bh_pass"] == True  # noqa: E712

    # p=0.008 fails Bonferroni (0.05/10=0.005) but passes BH (rank 2, crit 0.01).
    second = out[out["symbol"] == "S1"].iloc[0]
    assert second["search_wide_rank"] == 2
    assert second["search_wide_bonferroni_pass"] == False  # noqa: E712
    assert second["search_wide_bh_pass"] == True  # noqa: E712

    # p=0.02 at rank 3 has BH crit 0.015 -> fails; and BH is monotone so all
    # higher ranks fail too. Exactly 2 BH passes total.
    assert int(out["search_wide_bh_pass"].sum()) == 2
    assert int(out["search_wide_bonferroni_pass"].sum()) == 1


def test_cross_symbols_from_filter_extracts_symbol_and_fields():
    # Mixed filter: one cross-asset field + one ordinary field.
    filt = {
        "cross_USO_state_slope": "uptrend",
        "state_ext": "stretched_down",
    }
    assert cross_symbols_from_filter(filt) == {"USO": ["state_slope"]}

    # Two cross fields on the same conditioning symbol.
    filt2 = {
        "cross_USO_state_slope": "uptrend",
        "cross_USO_state_vol": "high_vol",
    }
    got = cross_symbols_from_filter(filt2)
    assert set(got.keys()) == {"USO"}
    assert sorted(got["USO"]) == ["state_slope", "state_vol"]

    # No cross fields -> empty dict (existing slices are unaffected).
    assert cross_symbols_from_filter({"state_ext": "neutral"}) == {}


def test_calibrate_stop_atr_mult_basic():
    from validate_slices import _calibrate_stop_atr_mult
    vals = [1.0, 1.2, 1.5, 2.0, 2.2, 2.5, 3.0, 3.2, 3.5, 4.0]
    res = _calibrate_stop_atr_mult(vals, percentile=0.90)
    assert res is not None
    assert 1.5 <= res <= 5.0
    assert _calibrate_stop_atr_mult([1.0, 2.0]) is None  # < 5 instances -> None


def test_annotate_search_wide_significance_dsr_matches_scalar_and_resists_tiny_n_outliers():
    from price.validation import deflated_sharpe

    lb = pd.DataFrame(
        [
            {
                "symbol": "STRONG",
                "valid_p_value_nw": 0.0001,
                "valid_n": 120,
                "valid_n_eff": 100.0,
                "valid_sharpe": 0.45,
                "valid_skew": 0.1,
                "valid_kurtosis": 3.2,
            },
            {
                "symbol": "MID",
                "valid_p_value_nw": 0.03,
                "valid_n": 80,
                "valid_n_eff": 70.0,
                "valid_sharpe": 0.20,
                "valid_skew": -0.8,
                "valid_kurtosis": 6.0,
            },
            {
                "symbol": "WEAK",
                "valid_p_value_nw": 0.40,
                "valid_n": 60,
                "valid_n_eff": 55.0,
                "valid_sharpe": 0.05,
                "valid_skew": 0.0,
                "valid_kurtosis": 3.0,
            },
            # Pathological n=2 outlier: counted in total trials M=4, but must NOT
            # blow up cross-trial V[SR] estimation.
            {
                "symbol": "TINY_OUTLIER",
                "valid_p_value_nw": 0.01,
                "valid_n": 2,
                "valid_n_eff": 2.0,
                "valid_sharpe": 50.0,
                "valid_skew": 0.0,
                "valid_kurtosis": 3.0,
            },
        ]
    )

    out = annotate_search_wide_significance(lb, p_threshold=0.05, min_samples=15)

    stable_sharpes = [0.45, 0.20, 0.05]
    expected_strong = deflated_sharpe(
        stable_sharpes,
        n_trials=4,
        observed_sr=0.45,
        n_obs=100.0,
        skewness=0.1,
        kurtosis=3.2,
    )
    expected_mid = deflated_sharpe(
        stable_sharpes,
        n_trials=4,
        observed_sr=0.20,
        n_obs=70.0,
        skewness=-0.8,
        kurtosis=6.0,
    )

    strong_row = out[out["symbol"] == "STRONG"].iloc[0]
    mid_row = out[out["symbol"] == "MID"].iloc[0]
    assert strong_row["search_wide_dsr"] == round(expected_strong, 6)
    assert bool(strong_row["search_wide_dsr_pass"]) is True
    assert mid_row["search_wide_dsr"] == round(expected_mid, 6)
    assert bool(mid_row["search_wide_dsr_pass"]) is False

    # Custom regime p_col without valid_sharpe must not inject search_wide_dsr
    # columns that could collide during regime-leaderboard joins.
    regime_frame = pd.DataFrame({"symbol": ["A", "B"], "__regime_p_value_nw__": [0.01, 0.50]})
    regime_out = annotate_search_wide_significance(regime_frame, p_col="__regime_p_value_nw__")
    assert "search_wide_dsr" not in regime_out.columns


def test_run_validation_purges_5_bars_by_default_and_emits_dsr_inputs(monkeypatch, tmp_path):
    import numpy as np
    from validate_slices import run_validation

    slices_csv = tmp_path / "discovered.csv"
    validated_csv = tmp_path / "validated.csv"
    monkeypatch.setattr("validate_slices.VALIDATED_SLICES_PATH", str(validated_csv))

    pd.DataFrame(
        [{"symbol": "SPY", "timeframe": "1d", "slice_combination": "state_ext=stretched_down", "side": "long"}]
    ).to_csv(slices_csv, index=False)

    rng = np.random.default_rng(42)
    # 100 bars -> 70/30 split; default 5-bar purge leaves 65 IS rows and 30 OOS rows.
    rets = rng.normal(0.015, 0.005, 100)
    # Spike the 5 boundary rows (65..69) to verify default purging excludes them.
    rets[65:70] = -0.90

    def fake_build_eligible_frame(symbol, timeframe, cross_symbols=None, bin_mode="insample"):
        return pd.DataFrame(
            {
                "bar_ts_utc": pd.date_range("2024-01-01", periods=100, freq="D", tz="UTC"),
                "fwd_ret_5": rets,
                "close_adj": [100.0] * 100,
                "state_ext": ["stretched_down"] * 100,
            }
        )

    monkeypatch.setattr("validate_slices.build_eligible_frame", fake_build_eligible_frame)

    purged_df = run_validation(slices_path=str(slices_csv), cost_bps=0.0, min_samples=10)
    unpurged_df = run_validation(slices_path=str(slices_csv), cost_bps=0.0, min_samples=10, purge_bars=0)

    assert int(purged_df.iloc[0]["train_n"]) == 65
    assert int(unpurged_df.iloc[0]["train_n"]) == 70
    assert purged_df.iloc[0]["verdict"] == "survived"
    assert unpurged_df.iloc[0]["verdict"] == "rejected"
    for col in ("valid_sharpe", "valid_skew", "valid_kurtosis", "valid_n_eff", "valid_psr", "overfit_ratio", "wf_oos_sharpe_slope"):
        assert col in purged_df.columns


def test_run_candidate_leaderboard_calibrates_horizon_return_and_stop_on_purged_train_only(monkeypatch, tmp_path):
    """best_fwd_horizon, expected_return, and best_stop_atr_mult must calibrate on
    the 20-bar-purged IS split (indices 0..49 on a 100-bar frame), ignoring both
    the 20-bar pre-OOS boundary (50..69) and the 30% OOS window (70..99)."""
    import numpy as np
    from validate_slices import run_candidate_leaderboard

    slices_csv = tmp_path / "discovered.csv"
    validated_csv = tmp_path / "validated.csv"
    leaderboard_csv = tmp_path / "leaderboard.csv"
    monkeypatch.setattr("validate_slices.VALIDATED_SLICES_PATH", str(validated_csv))

    pd.DataFrame(
        [{"symbol": "SPY", "timeframe": "1d", "slice_combination": "state_ext=stretched_down", "side": "long"}]
    ).to_csv(slices_csv, index=False)

    # 100 bars: purged IS for max(HORIZONS)=20 is indices 0..49.
    # In 0..49: fwd_ret_5 = 0.02 beats fwd_ret_20 = 0.01, and MAE ATR is steady 1.8.
    # In 50..99 (boundary + OOS): fwd_ret_20 = 0.50 and MAE ATR = 4.9 — if full
    # history leaked, best_fwd_horizon would flip to 20, expected_return > 0.25,
    # and best_stop_atr_mult would jump near 4.9.
    fwd_5 = np.full(100, 0.02)
    fwd_20 = np.concatenate([np.full(50, 0.01), np.full(50, 0.50)])
    mae_5 = np.concatenate([np.full(50, 1.8), np.full(50, 4.9)])
    mae_20 = np.concatenate([np.full(50, 1.8), np.full(50, 4.9)])

    def fake_build_eligible_frame(symbol, timeframe, cross_symbols=None, bin_mode="insample"):
        return pd.DataFrame(
            {
                "bar_ts_utc": pd.date_range("2024-01-01", periods=100, freq="D", tz="UTC"),
                "fwd_ret_3": np.full(100, 0.005),
                "fwd_ret_5": fwd_5,
                "fwd_ret_10": np.full(100, 0.012),
                "fwd_ret_20": fwd_20,
                "fwd_mae_atr_5_long": mae_5,
                "fwd_mae_atr_20_long": mae_20,
                "close_adj": [100.0] * 100,
                "high": [101.0] * 100,
                "low": [99.0] * 100,
                "close": [100.0] * 100,
                "volume": [1_000_000.0] * 100,
                "state_ext": ["stretched_down"] * 100,
            }
        )

    monkeypatch.setattr("validate_slices.build_eligible_frame", fake_build_eligible_frame)

    lb = run_candidate_leaderboard(
        slices_path=str(slices_csv),
        output_path=str(leaderboard_csv),
        cost_bps=0.0,
        min_samples=10,
    )

    row = lb.iloc[0]
    assert int(row["best_fwd_horizon"]) == 5
    assert abs(float(row["expected_return"]) - 0.02) < 1e-9
    assert abs(float(row["best_stop_atr_mult"]) - 1.8) < 1e-6



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
    summarize_baseline_train_valid,
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


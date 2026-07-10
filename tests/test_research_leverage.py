"""Tests for leverage-aware research lifecycle metadata."""

from pathlib import Path
import sys

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.research_leverage import (  # noqa: E402
    evaluate_candidate_leverage,
    evaluate_leverage_scenario,
)


def test_overnight_two_x_is_compatible_but_four_x_requires_flatten():
    candidate = {"valid_mean_ret_costadj": 0.02}
    two_x = evaluate_leverage_scenario(candidate, 2.0)
    four_x = evaluate_leverage_scenario(candidate, 4.0)
    assert two_x.overnight_compatible is True
    assert two_x.requires_same_day_flatten is False
    assert four_x.overnight_compatible is False
    assert four_x.requires_same_day_flatten is True
    assert four_x.risk_status == "fail_overnight_constraint"


def test_missing_atr_risk_never_passes_auto_promotion_gate():
    result = evaluate_candidate_leverage({"valid_mean_ret_costadj": 0.02})
    assert result["leverage_auto_promotion_gate"] is False
    assert result["leverage_1x_risk_status"] == "unknown_missing_atr_risk"
    assert result["leverage_2x_risk_status"] == "unknown_missing_atr_risk"
    assert result["leverage_4x_requires_same_day_flatten"] is True


def test_aggregate_risk_can_fail_a_leverage_scenario():
    scenario = evaluate_leverage_scenario(
        {"valid_mean_ret_costadj": 0.02},
        2.0,
        equity=100_000.0,
        atr_risk_dollars=4_000.0,
        max_aggregate_risk_pct=0.03,
    )
    assert scenario.risk_status == "fail_aggregate_risk"

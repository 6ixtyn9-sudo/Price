"""Leverage scenario evaluation for research candidates.

Leverage does not create an edge; it scales exposure, margin usage, and risk.
This module keeps raw discovery leverage-neutral and adds explicit scenario
metadata at the lifecycle/promotion boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LeverageScenario:
    multiple: float
    overnight_hold: bool
    equity: float
    max_notional_per_position: float
    expected_return: Optional[float]
    expected_pnl_at_position_cap: Optional[float]
    theoretical_return_on_equity: Optional[float]
    overnight_compatible: bool
    requires_same_day_flatten: bool
    margin_cushion_fraction: Optional[float]
    risk_status: str

    def to_dict(self, prefix: str = "") -> dict:
        prefix = prefix or f"leverage_{self.multiple:g}x_"
        return {
            f"{prefix}multiple": self.multiple,
            f"{prefix}expected_return": self.expected_return,
            f"{prefix}expected_pnl_at_position_cap": self.expected_pnl_at_position_cap,
            f"{prefix}theoretical_return_on_equity": self.theoretical_return_on_equity,
            f"{prefix}overnight_compatible": self.overnight_compatible,
            f"{prefix}requires_same_day_flatten": self.requires_same_day_flatten,
            f"{prefix}margin_cushion_fraction": self.margin_cushion_fraction,
            f"{prefix}risk_status": self.risk_status,
        }


def _float_or_none(value) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def evaluate_leverage_scenario(
    candidate,
    multiple: float,
    equity: float = 100_000.0,
    max_notional_per_position: float = 2_500.0,
    overnight_hold: bool = True,
    margin_cushion_pct: float = 0.20,
    atr_risk_dollars: Optional[float] = None,
    max_aggregate_risk_pct: float = 0.03,
) -> LeverageScenario:
    """Evaluate one candidate under a leverage scenario.

    This is deliberately conservative:
      * overnight positions are compatible with at most 2x;
      * 4x is marked as requiring same-day flattening;
      * missing ATR/R-risk data is ``unknown``, never silently passed;
      * return scaling is presented as a scenario, not as evidence of edge.
    """
    multiple = float(multiple)
    equity = float(equity)
    max_notional_per_position = float(max_notional_per_position)
    expected_return = _float_or_none(candidate.get("valid_mean_ret_costadj"))
    expected_pnl = (
        expected_return * max_notional_per_position
        if expected_return is not None else None
    )
    theoretical_return = expected_return * multiple if expected_return is not None else None
    overnight_compatible = (not overnight_hold) or multiple <= 2.0
    requires_flatten = overnight_hold and multiple > 2.0
    margin_cushion = None
    if equity > 0 and multiple > 0:
        ceiling = equity * multiple
        margin_cushion = 1.0 - (max_notional_per_position / ceiling)

    if atr_risk_dollars is None:
        risk_status = "unknown_missing_atr_risk"
    else:
        risk_budget = equity * max_aggregate_risk_pct
        risk_status = "pass" if atr_risk_dollars <= risk_budget else "fail_aggregate_risk"

    if not overnight_compatible:
        risk_status = "fail_overnight_constraint"

    return LeverageScenario(
        multiple=multiple,
        overnight_hold=overnight_hold,
        equity=equity,
        max_notional_per_position=max_notional_per_position,
        expected_return=expected_return,
        expected_pnl_at_position_cap=expected_pnl,
        theoretical_return_on_equity=theoretical_return,
        overnight_compatible=overnight_compatible,
        requires_same_day_flatten=requires_flatten,
        margin_cushion_fraction=margin_cushion,
        risk_status=risk_status,
    )


def evaluate_candidate_leverage(candidate, **kwargs) -> dict:
    """Return lifecycle-ready 1x/2x/4x scenario metadata."""
    scenarios = {
        f"{multiple:g}x": evaluate_leverage_scenario(candidate, multiple, **kwargs)
        for multiple in (1.0, 2.0, 4.0)
    }
    result = {}
    for name, scenario in scenarios.items():
        result.update(scenario.to_dict(prefix=f"leverage_{name}_"))
    result["leverage_overnight_max_multiple"] = 2.0
    result["leverage_auto_promotion_gate"] = all(
        scenarios[name].risk_status == "pass"
        and scenarios[name].overnight_compatible
        for name in ("1x", "2x")
    )
    result["leverage_4x_requires_same_day_flatten"] = scenarios["4x"].requires_same_day_flatten
    return result

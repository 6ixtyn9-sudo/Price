"""Tests for the realistic execution cost model (lever 4).

Covers:
  - CostModel arithmetic (leg, round-trip, drag in return units, validation
    per-leg mapping, apply to a return series).
  - compute_conviction cost-negation: net_edge <= 0 -> conviction floored to
    0.05 with mode='cost_negated' (NOT rescued by the known-edge floor).
  - compute_conviction cost reduces conviction monotonically with drag.
  - cost_model=None preserves pre-lever-4 behaviour exactly.
  - PositionSize carries expected_cost_bps_round_trip for attribution.
  - The default model's realistic impact on the actual Tier-1 edges.

Pure unit tests; no network, no credentials, no warehouse.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.cost_model import CostModel, default_cost_model  # noqa: E402
from price.sizing import SliceEdge, compute_conviction, compute_position_size  # noqa: E402


@dataclass
class _Limits:
    max_notional_per_position: float = 2500.0
    conviction_sizing_enabled: bool = True
    risk_fraction_per_trade: float = 0.0
    account_equity_for_sizing: object = None


# ---------------------------------------------------------------------------
# CostModel arithmetic
# ---------------------------------------------------------------------------

def test_default_round_trip_is_conservative():
    cm = default_cost_model()
    # spread 1 + slippage 3 = 4bp/leg, round trip 8bp.
    assert cm.leg_bps() == 4.0
    assert cm.round_trip_bps() == 8.0
    assert cm.round_trip_drag() == 0.0008


def test_per_leg_bps_for_validation_maps_to_validation_cost_bps():
    """validation.apply_transaction_cost takes cost_bps PER LEG and doubles it
    for round_trip. So passing this value as --cost-bps reproduces the model."""
    cm = CostModel(commission_bps=0.5, spread_bps=1.0, slippage_bps=2.0)
    assert cm.per_leg_bps_for_validation() == 3.5  # validation --cost-bps 3.5


def test_round_trip_false_halves_drag():
    rt = CostModel(spread_bps=2.0, slippage_bps=0.0)
    one_way = CostModel(spread_bps=2.0, slippage_bps=0.0, round_trip=False)
    assert rt.round_trip_bps() == 4.0
    assert one_way.round_trip_bps() == 2.0


def test_apply_subtracts_drag_from_series():
    cm = CostModel(spread_bps=5.0, slippage_bps=0.0)  # 10bp round trip = 0.0010 drag
    out = list(cm.apply(pd.Series([0.01, 0.02, 0.03])))
    assert abs(out[0] - 0.009) < 1e-9
    assert abs(out[1] - 0.019) < 1e-9
    assert abs(out[2] - 0.029) < 1e-9


def test_to_dict_is_flat_and_csv_safe():
    d = default_cost_model().to_dict()
    for k in ["commission_bps", "spread_bps", "slippage_bps", "round_trip",
              "leg_bps", "round_trip_bps"]:
        assert k in d
    assert all(isinstance(v, (int, float, bool)) for v in d.values())


# ---------------------------------------------------------------------------
# compute_conviction cost-negation
# ---------------------------------------------------------------------------

def _strong_edge(mean=0.03):
    return SliceEdge(
        mean_return=mean, excess_vs_parent=0.005,
        walk_forward_pass_count=3, scenario_survived_count=6, valid_n=60,
        search_wide_bh_pass=True, search_wide_bonferroni_pass=False,
    )


def test_cost_negates_edge_to_near_zero_conviction():
    """A thin edge (here 0.08% = 8bp) that is eaten by execution cost earns
    ~no capital and is NOT rescued by the known-edge floor."""
    edge = _strong_edge(mean=0.0008)  # 0.08% gross edge
    cm = CostModel(spread_bps=3.0, slippage_bps=3.0)  # 12bp round trip = 0.0012
    cr = compute_conviction(edge, cost_model=cm)
    assert cr.mode == "cost_negated"
    assert cr.conviction == 0.05
    assert cr.components["net_edge"] < 0


def test_cost_reduces_conviction_monotonically():
    edge = _strong_edge(mean=0.03)
    none = compute_conviction(edge, cost_model=None).conviction
    small = compute_conviction(edge, cost_model=CostModel(spread_bps=1.0, slippage_bps=1.0)).conviction
    large = compute_conviction(edge, cost_model=CostModel(spread_bps=2.0, slippage_bps=4.0)).conviction
    assert none >= small >= large


def test_cost_model_none_preserves_pre_lever4_behavior():
    """compute_conviction(edge) with no cost_model must equal the old behavior."""
    edge = _strong_edge(mean=0.03)
    cr = compute_conviction(edge)  # cost_model defaults to None
    assert "net_edge" not in cr.components
    assert cr.mode == "leaderboard_backed"


def test_strong_edge_survives_default_cost():
    """KLAC-scale edge (4.7%) should comfortably clear the 8bp default drag."""
    klac = SliceEdge(
        mean_return=0.0468, excess_vs_parent=0.0079,
        walk_forward_pass_count=3, scenario_survived_count=8, valid_n=45,
        search_wide_bh_pass=True, search_wide_bonferroni_pass=True,
    )
    cr = compute_conviction(klac, cost_model=default_cost_model())
    assert cr.mode == "leaderboard_backed"
    assert cr.conviction > 0.5
    assert cr.components["net_edge"] > 0


# ---------------------------------------------------------------------------
# PositionSize carries expected cost for attribution
# ---------------------------------------------------------------------------

def test_position_size_records_expected_cost():
    cm = CostModel(spread_bps=2.0, slippage_bps=3.0)  # 10bp round trip
    size = compute_position_size(
        "XLF", "1d", "state_ext=stretched_up + state_slope=flat", 100.0,
        _Limits(), leaderboard_path=Path("/nonexistent/lb.csv"), cost_model=cm,
    )
    d = size.to_audit_dict()
    assert d["sizing_expected_cost_bps_rt"] == 10.0


def test_zero_cost_model_reproduces_no_cost_sizing():
    """A zero-drag CostModel == no cost adjustment (net edge == gross edge)."""
    edge = _strong_edge(mean=0.03)
    zero = compute_conviction(edge, cost_model=CostModel(0, 0, 0))
    none = compute_conviction(edge, cost_model=None)
    assert zero.conviction == none.conviction
    assert zero.mode == "leaderboard_backed"


# ---------------------------------------------------------------------------
# Demonstration: default cost model on the real Tier-1 edges
# ---------------------------------------------------------------------------

def test_default_cost_on_tier1_does_not_negate_any_tier1_edge():
    """All four corrected Tier-1 daily edges must still be net-positive under
    the conservative 8bp default (they range 1.0%-4.7%). Pins that the default
    is conservative-but-not-negating for the live book's actual edges."""
    cm = default_cost_model()
    tier1 = {
        "KLAC": 0.0468, "XOP": 0.0184, "XLB": 0.0152, "XLF": 0.0100,
    }
    for sym, mean in tier1.items():
        edge = SliceEdge(
            mean_return=mean, excess_vs_parent=0.004,
            walk_forward_pass_count=3, scenario_survived_count=7, valid_n=50,
            search_wide_bh_pass=True, search_wide_bonferroni_pass=False,
        )
        cr = compute_conviction(edge, cost_model=cm)
        assert cr.mode != "cost_negated", f"{sym} negated by default cost"
        assert cr.components["net_edge"] > 0, f"{sym} net edge <= 0"

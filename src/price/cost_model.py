"""Realistic execution cost model.

This module is the single source of truth for "what does a real fill cost,"
used by position sizing (lever 1 -> net the execution drag from the edge
before conviction), P&L attribution (lever 5 -> compare expected vs realized
cost), and optionally validation alignment (re-rank slices at realistic cost).

Why it exists
The research truth (validation) measures edges at ~1bp/leg (~2bp round trip).
The live execution path previously used ZERO cost: sizing assumed you fill at
the bar close with no spread, no slippage. That is the gap between backtested
ROI and realized ROI. The biggest term is the signal-to-fill gap -- a signal
fires on a closed bar (e.g. 2026-07-02) and the market order fills at the next
session open (e.g. 2026-07-06+). Validation assumes transacting at the signal
bar's close; the live workflow does not. This model stands in for that gap
conservatively until enough fills accumulate to calibrate slippage to realized
values (the lever-5 attribution workstream).

Design choices (all deliberate, all honest)
- NOT a per-symbol cost table. A hand-kept magic-number list of spreads per
  ticker would be exactly the overfit / hand-authored risk this project
  rejects. Instead: a decomposed model (commission + spread + slippage) with
  conservative defaults tuned to the monitored set's character (liquid-to-
  mid US equities/ETFs, market orders), configurable by the operator.
- Defaults are deliberately conservative (pessimistic for SPY/XLF, about
  right for XOP/KLAC). Better to under-size a marginal trade than over-size
  it; the operator can lower the model for ultra-liquid names.
- round_trip drag is subtracted from the validation-cost-adjusted edge
  (valid_mean_ret_costadj). There is a small (~2bp) conservative overlap with
  validation's own cost; this is intentional and documented, never corrected
  away, because netting the full execution drag guarantees we never size a
  trade that cannot clear its real-world cost.

Doctrine: this module makes NO edge or promotion claim. It only models the
friction between a backtested return and a realized one.
"""

from dataclasses import dataclass


# Conservative defaults for a retail MARKET order on liquid-to-mid US
# equities/ETFs (the monitored set is XOP/XLB/KLAC/XLF/SPY/XLK). These are
# NOT measured; they are honest placeholders to be calibrated from realized
# fills once the book produces round-trips (lever 5).
DEFAULT_COMMISSION_BPS = 0.0   # zero-commission retail / Alpaca paper
DEFAULT_SPREAD_BPS = 1.5       # half-spread paid crossing a market order;
                               # liquid (SPY/XLF) ~0.4-1bp, XOP/KLAC wider
DEFAULT_SLIPPAGE_BPS = 5.0     # adverse fill + the signal-to-fill gap.
                               # With Limit Orders at signal close, this
                               # should be much lower than the 161bp market
                               # order gap, but we keep 5bp for 'touch' friction.


@dataclass
class CostModel:
    """Decomposed realistic per-leg execution cost, in basis points.

    All components are PER LEG. round_trip=True (default) doubles them because
    a position pays on both entry and exit.
    """

    commission_bps: float = DEFAULT_COMMISSION_BPS
    spread_bps: float = DEFAULT_SPREAD_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    round_trip: bool = True

    def leg_bps(self) -> float:
        """Total per-leg cost in basis points (commission + spread + slippage)."""
        return self.commission_bps + self.spread_bps + self.slippage_bps

    def round_trip_bps(self) -> float:
        """Total round-trip cost in basis points (entry + exit legs)."""
        return self.leg_bps() * (2.0 if self.round_trip else 1.0)

    def round_trip_drag(self) -> float:
        """Round-trip cost as a return drag (e.g. 8bp -> 0.0008).

        Subtract this from a return/edge to get the net-of-cost tradeable
        return. This is the number sizing uses to net the execution drag off
        the validation-cost-adjusted edge.
        """
        return self.round_trip_bps() / 10000.0

    def per_leg_bps_for_validation(self) -> float:
        """Per-leg bps in the units validation's `cost_bps` parameter expects.

        validation.apply_transaction_cost takes cost_bps PER LEG and applies
        round_trip internally. So to make validation use this model, pass
        --cost-bps <this value>. This lets research and execution share one
        cost truth without flipping validation's default (which is a
        separate, operator-owned re-ranking decision).
        """
        return self.leg_bps()

    def apply(self, returns):
        """Subtract the round-trip drag from a return series.

        Mirrors validation.apply_transaction_cost's contract so the same
        drag logic is available to execution-side code. Returns a pd.Series.
        """
        import pandas as pd  # local import keeps the module importable headless
        s = pd.Series(returns).astype(float)
        return s - self.round_trip_drag()

    def to_dict(self) -> dict:
        return {
            "commission_bps": self.commission_bps,
            "spread_bps": self.spread_bps,
            "slippage_bps": self.slippage_bps,
            "round_trip": self.round_trip,
            "leg_bps": self.leg_bps(),
            "round_trip_bps": self.round_trip_bps(),
        }


def default_cost_model() -> CostModel:
    """The conservative default CostModel used when the operator sets none."""
    return CostModel()

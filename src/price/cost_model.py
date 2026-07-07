from dataclasses import dataclass
DEFAULT_COMMISSION_BPS = 0.0
DEFAULT_SPREAD_BPS = 1.5
DEFAULT_SLIPPAGE_BPS = 5.0
@dataclass
class CostModel:
    commission_bps: float = DEFAULT_COMMISSION_BPS
    spread_bps: float = DEFAULT_SPREAD_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    round_trip: bool = True
    def leg_bps(self) -> float: return self.commission_bps + self.spread_bps + self.slippage_bps
    def round_trip_bps(self) -> float: return self.leg_bps() * (2.0 if self.round_trip else 1.0)
    def round_trip_drag(self) -> float: return self.round_trip_bps() / 10000.0
    def per_leg_bps_for_validation(self) -> float: return self.leg_bps()
    def apply(self, returns):
        import pandas as pd
        s = pd.Series(returns).astype(float)
        return s - self.round_trip_drag()
    def to_dict(self) -> dict:
        return {"commission_bps": self.commission_bps, "spread_bps": self.spread_bps, "slippage_bps": self.slippage_bps, "round_trip": self.round_trip, "leg_bps": self.leg_bps(), "round_trip_bps": self.round_trip_bps()}
def default_cost_model() -> CostModel: return CostModel()

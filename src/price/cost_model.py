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


# ── Per-lane cost assumptions ─────────────────────────────────────────────────
# Equity values are the long-standing system-wide defaults (13 bps round
# trip) — unchanged, so the equity book's validation/live behavior is
# bit-identical. Crypto and futures lanes previously inherited those equity
# numbers in attribution, flattering their net_of_cost figures. The lane
# values below are conservative FIRST-PASS placeholders pending calibration
# from realized per-lane fill gaps at the next observation review; override
# centrally here, never per call-site.
LANE_COST_BPS = {
    "equity":  {"commission_bps": 0.0, "spread_bps": 1.5, "slippage_bps": 5.0},
    "crypto":  {"commission_bps": 0.0, "spread_bps": 8.0, "slippage_bps": 12.0},
    "futures": {"commission_bps": 2.0, "spread_bps": 2.0, "slippage_bps": 6.0},
}


def cost_model_for_lane(lane: str) -> CostModel:
    params = LANE_COST_BPS.get(str(lane).strip().lower(), LANE_COST_BPS["equity"])
    return CostModel(**params)


def lane_for_symbol(symbol: str) -> str:
    # Function-local import: config is imported almost everywhere;
    # cost_model must never create an import cycle for it.
    from price.config import is_crypto, is_futures
    if is_crypto(symbol):
        return "crypto"
    if is_futures(symbol):
        return "futures"
    return "equity"


def cost_model_for_symbol(symbol: str) -> CostModel:
    return cost_model_for_lane(lane_for_symbol(symbol))

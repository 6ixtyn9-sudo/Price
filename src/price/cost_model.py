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


# ── Cost basis: equity is the only active lane ────────────────────────────────
# The equity values are the long-standing system-wide conservative assumptions
# (13 bps round trip) — unchanged, so the live book's behavior is bit-identical.
# They are ASSUMPTIONS WITH A MEASUREMENT LOOP, not guesses: attribution's
# realized signal-to-fill gap accumulates against them and replaces them at the
# maturity review once enough round-trips exist.
# No constants exist for crypto or futures. Those lanes are inactive, and
# shipping invented numbers would let backtests/attribution quote fabricated
# net_of_cost figures for markets we do not trade — a worse failure than an
# honest refusal. Requests for an inactive lane raise UnsupportedLaneError
# here; attribution degrades those slices to a null net with an explicit note
# instead. If a lane ever activates, its constants are calibrated from realized
# fills BEFORE the first order — introduced in the same commit that activates
# it, never carried as guesses in the meantime.
EQUITY_COST_BPS = {
    "commission_bps": DEFAULT_COMMISSION_BPS,
    "spread_bps": DEFAULT_SPREAD_BPS,
    "slippage_bps": DEFAULT_SLIPPAGE_BPS,
}


class UnsupportedLaneError(ValueError):
    """No cost basis exists for the requested lane (inactive: crypto/futures)."""


def cost_model_for_lane(lane: str) -> CostModel:
    key = str(lane).strip().lower()
    if key == "equity":
        return CostModel(**EQUITY_COST_BPS)
    # Fail LOUD, never fall back silently: an unchecked .get(lane, equity)
    # here previously meant a typo'd or inactive lane was priced with equity
    # assumptions — a fabricated number wearing a badge.
    raise UnsupportedLaneError(
        f"no cost basis exists for lane {key!r}: equity is the only active "
        "lane; inactive lanes must be calibrated from realized fills before "
        "first order, not priced from guesswork"
    )


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

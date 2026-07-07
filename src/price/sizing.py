"""Edge- and volatility-aware position sizing.

This module replaces the original equal-notional sizing rule
(``floor(max_notional / price)``) with a two-stage model:

  Stage A - conviction-weighted notional:
      target_notional = conviction * max_notional_per_position
      qty_notional    = floor(target_notional / price)

      Conviction is derived from the slice's research edge metrics
      (magnitude, walk-forward robustness, scenario survival, sample
      adequacy, parent-relative excess, multiple-testing survival). A
      stronger, more robust, search-wide-defensible edge earns more
      capital than a weaker one at the same price.

  Stage B - volatility normalization (risk rail):
      risk_dollars = conviction * risk_fraction_per_trade * equity
      qty_risk     = floor(risk_dollars / atr)

      When account equity and a 14-bar ATR are available, qty is the
      MIN of the conviction-notional qty and the risk-based qty, so a
      high-volatility name cannot concentrate more than its risk budget
      allows. On small paper notional caps this rail rarely binds; it
      becomes the primary knob as we move toward real capital.

Graceful degradation (this is the safety property):
  * No candidate leaderboard present  -> conviction = 1.0 (neutral),
    which reproduces the ORIGINAL equal-notional behavior exactly.
    Sizing only deviates from equal-notional when we actually have the
    edge data to justify it.
  * No warehouse / no ATR            -> Stage B skipped, Stage A only.
  * No account equity configured     -> Stage B skipped, Stage A only.
  * price <= 0 / non-finite          -> qty 0.

Doctrine: this module does NOT decide when to trade (monitor.py), does
NOT place orders (trading.py), and makes NO edge/promotion claim. It
only sizes positions for slices that monitor.py has already matched and
the risk gate has already allowed. Every decision it makes is returned
in the PositionSize breakdown so paper_trade.py can audit it.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from price.config import DATA_DIR
from price.cost_model import CostModel, default_cost_model


CANDIDATE_LEADERBOARD_PATH = DATA_DIR / "candidate_leaderboard.csv"

# Curated, execution-owned edge metrics for the monitored set. Written by the
# operator from validated research artifacts (see HANDOVER). Checked BEFORE
# candidate_leaderboard.csv so research reruns that overwrite the leaderboard
# (e.g. the KLAC sector-spread diagnostic) cannot silently zero out live
# conviction sizing.
MONITORED_EDGE_METRICS_PATH = DATA_DIR / "monitored_edge_metrics.csv"

# ---- conviction model constants (all dimensionless, in [0,1] space) ----
# Reference edge magnitude that maps to "full" magnitude weight. The
# corrected liquid236 Tier-1 daily edges range ~1.0% (XLF) to ~4.7%
# (KLAC); 3% is a sensible "strong" anchor so KLAC scales high and XLF
# scales lower, without either saturating trivially.
EDGE_REF = 0.03
# Reference parent-relative excess that maps to "fully independent".
# Tier-1 excess-vs-best-parent values are ~0.2%-0.8%; 1% is a generous
# anchor so clearing the parent bar strongly but does not by itself
# drive conviction to the ceiling.
EXCESS_REF = 0.01
# Project's existing validation sample floor.
MIN_SAMPLES = 15
# Observed maxima used to normalize robustness components.
MAX_SCENARIOS = 8
MAX_WF = 4

# Neutral conviction used when no leaderboard edge data is available.
# Set to 1.0 so the degraded path reproduces the original equal-notional
# sizing and does NOT silently halve the live paper book.
NEUTRAL_CONVICTION = 1.0

# Floor for a *known* (leaderboard-backed) but weak edge, so the weakest
# clean survivor still gets a meaningful slice of capital rather than ~0.
KNOWN_CONVICTION_FLOOR = 0.35

# Small bonuses for surviving multiple-testing correction. Bonferroni is
# rare and strong (search-wide strictest bar); BH is the looser FDR bar.
BONUS_BONFERRONI = 1.15
BONUS_BH = 1.05


@dataclass
class SliceEdge:
    """Conviction-relevant metrics for one (symbol, timeframe, slice).

    Mirrors the candidate_leaderboard.csv schema produced by
    scripts/validate_slices.py. All numeric fields are in decimal return
    units (e.g. 0.0184 == 1.84%) where applicable, or counts.
    """

    mean_return: float             # valid_mean_ret_costadj (cost-adjusted edge)
    excess_vs_parent: float        # valid_excess_vs_best_parent
    walk_forward_pass_count: int   # 0..4
    scenario_survived_count: int   # 0..~8
    valid_n: int                   # validation sample size
    search_wide_bh_pass: bool
    search_wide_bonferroni_pass: bool
    triage_bucket: str = ""

    def to_dict(self) -> dict:
        return {
            "mean_return": self.mean_return,
            "excess_vs_parent": self.excess_vs_parent,
            "walk_forward_pass_count": self.walk_forward_pass_count,
            "scenario_survived_count": self.scenario_survived_count,
            "valid_n": self.valid_n,
            "search_wide_bh_pass": self.search_wide_bh_pass,
            "search_wide_bonferroni_pass": self.search_wide_bonferroni_pass,
            "triage_bucket": self.triage_bucket,
        }


@dataclass
class ConvictionResult:
    """Outcome of compute_conviction: a scalar in (0,1] plus a full
    breakdown so the audit trail shows exactly why capital was scaled."""

    conviction: float
    mode: str                      # "leaderboard_backed" | "neutral_no_data"
    components: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "conviction": round(self.conviction, 5),
            "mode": self.mode,
            "components": self.components,
            "reasons": self.reasons,
        }


@dataclass
class PositionSize:
    """Outcome of compute_position_size."""

    qty: int
    conviction: float
    sizing_mode: str               # "conviction_with_vol_rail" | "conviction_notional_only" | "fallback_no_data" | "cost_negated" | "zero"
    target_notional: float
    qty_notional: int
    qty_risk: Optional[int]
    atr: Optional[float]
    conviction_result: Optional[ConvictionResult]
    expected_cost_bps_round_trip: float = 0.0
    reasons: list = field(default_factory=list)

    def to_audit_dict(self) -> dict:
        """Flat fields safe to splat into the paper_trade audit CSV."""
        return {
            "sizing_mode": self.sizing_mode,
            "sizing_conviction": round(self.conviction, 5),
            "sizing_target_notional": round(self.target_notional, 2) if self.target_notional == self.target_notional else None,
            "sizing_atr": (round(self.atr, 4) if self.atr is not None else None),
            "sizing_qty_notional": self.qty_notional,
            "sizing_qty_risk": self.qty_risk,
            "sizing_expected_cost_bps_rt": round(self.expected_cost_bps_round_trip, 2),
        }


def _clip(x: float, lo: float, hi: float) -> float:
    if x != x:  # NaN guard
        return lo
    return max(lo, min(hi, x))


def compute_conviction(
    edge: Optional[SliceEdge],
    cost_model: Optional[CostModel] = None,
) -> ConvictionResult:
    """Map a slice's research edge metrics to a conviction weight in (0,1].

    Pure function: deterministic, no I/O, no network, no warehouse read.

    magnitude is computed on the NET-OF-EXECUTION-COST edge: the cost_model's
    round-trip drag is subtracted from the validation-cost-adjusted edge
    first, so a trade whose edge is eaten by realistic execution cost earns
    near-zero capital regardless of its other metrics. cost_model has NO
    effect on the no-data path (we cannot net cost from an edge we lack).

    Cost overlap note (deliberately conservative): valid_mean_ret_costadj is
    already net of ~2bp validation cost. Subtracting the full execution drag
    double-counts that ~2bp. Intentional -- guarantees we never size a trade
    that cannot clear its real-world cost.
    """
    if edge is None:
        return ConvictionResult(
            conviction=NEUTRAL_CONVICTION,
            mode="neutral_no_data",
            components={},
            reasons=["no leaderboard edge data; using neutral conviction (reproduces equal-notional)"],
        )

    # Net the execution drag off the validation-cost-adjusted edge.
    exec_drag = cost_model.round_trip_drag() if cost_model else 0.0
    net_edge = edge.mean_return - exec_drag

    # A cost-negated edge earns ~no capital and is explicitly NOT rescued by
    # the KNOWN_CONVICTION_FLOOR (a cost-eating trade is not a survivor
    # net-of-cost). Tiny epsilon so it is distinguishable from a hard zero
    # without deploying meaningful capital.
    if net_edge <= 0:
        return ConvictionResult(
            conviction=0.05,
            mode="cost_negated",
            components={
                "gross_edge": round(edge.mean_return, 6),
                "exec_drag": round(exec_drag, 6),
                "net_edge": round(net_edge, 6),
            },
            reasons=[
                f"cost-negated: gross edge {edge.mean_return:.4f} - exec drag "
                f"{exec_drag:.4f} = net {net_edge:.4f} <= 0; conviction floored to 0.05",
            ],
        )

    magnitude = _clip(net_edge / EDGE_REF, 0.1, 1.0)

    wf_rate = edge.walk_forward_pass_count / MAX_WF if edge.walk_forward_pass_count > 0 else 0.0
    scenario_rate = edge.scenario_survived_count / MAX_SCENARIOS if edge.scenario_survived_count > 0 else 0.0
    sample_rate = min(1.0, edge.valid_n / MIN_SAMPLES) if edge.valid_n > 0 else 0.0
    robustness = _clip(
        0.40 * wf_rate + 0.40 * scenario_rate + 0.20 * sample_rate,
        0.0, 1.0,
    )

    validity = _clip(0.5 + 0.5 * min(1.0, edge.excess_vs_parent / EXCESS_REF), 0.0, 1.0)

    conviction = magnitude * robustness * validity

    bonus = 1.0
    mt_note = "none"
    if edge.search_wide_bonferroni_pass:
        bonus = BONUS_BONFERRONI
        mt_note = "bonferroni"
    elif edge.search_wide_bh_pass:
        bonus = BONUS_BH
        mt_note = "bh"
    conviction *= bonus

    conviction = _clip(conviction, KNOWN_CONVICTION_FLOOR, 1.0)

    components = {
        "magnitude": round(magnitude, 5),
        "robustness": round(robustness, 5),
        "validity": round(validity, 5),
        "wf_rate": round(wf_rate, 5),
        "scenario_rate": round(scenario_rate, 5),
        "sample_rate": round(sample_rate, 5),
        "mt_bonus": round(bonus, 5),
        "mt_note": mt_note,
    }
    if exec_drag > 0:
        components.update({
            "gross_edge": round(edge.mean_return, 6),
            "exec_drag": round(exec_drag, 6),
            "net_edge": round(net_edge, 6),
        })

    edge_str = f"net edge {net_edge:.4f}"
    drag_str = f"; gross {edge.mean_return:.4f} - drag {exec_drag:.4f}" if exec_drag > 0 else ""
    reasons = [
        f"magnitude={magnitude:.3f} ({edge_str} / ref {EDGE_REF}{drag_str})",
        f"robustness={robustness:.3f} (wf {edge.walk_forward_pass_count}/{MAX_WF}, "
        f"scenario {edge.scenario_survived_count}/{MAX_SCENARIOS}, n {edge.valid_n})",
        f"validity={validity:.3f} (parent excess {edge.excess_vs_parent:.4f})",
        f"mt_bonus={bonus:.3f} ({mt_note})",
    ]

    return ConvictionResult(
        conviction=conviction,
        mode="leaderboard_backed",
        components=components,
        reasons=reasons,
    )


def compute_atr_14(df: pd.DataFrame) -> Optional[float]:
    """14-bar Average True Range in price units, from the latest row.

    Mirrors the TR/ATR definition in features.py but returns the actual
    ATR value (features.py only stores the normalized variant). Returns
    None if there is not enough data or the latest ATR is non-finite.
    """
    if df is None or df.empty:
        return None
    need = {"high_adj", "low_adj", "close_adj"}
    if not need.issubset(df.columns):
        return None
    d = df.sort_values("bar_ts_utc").reset_index(drop=True)
    if len(d) < 15:
        return None
    high = d["high_adj"]
    low = d["low_adj"]
    close = d["close_adj"]
    if close.isna().any() or high.isna().any() or low.isna().any():
        return None
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14).mean()
    val = atr.iloc[-1]
    try:
        val = float(val)
    except (TypeError, ValueError):
        return None
    if val != val or val <= 0:
        return None
    return val


def _resolve_atr(
    symbol: str,
    timeframe: str,
    atr: Optional[float],
) -> Optional[float]:
    """Use a passed ATR if given; otherwise best-effort read the local
    warehouse and compute it. Never raises."""
    if atr is not None:
        try:
            v = float(atr)
            return v if (v == v and v > 0) else None
        except (TypeError, ValueError):
            return None
    try:
        from price.warehouse import load_from_warehouse
        df = load_from_warehouse(symbol, timeframe)
        return compute_atr_14(df)
    except Exception:  # noqa: BLE001 - sizing must never crash the scan
        return None


def load_edge_metrics(
    symbol: str,
    timeframe: str,
    slice_combination: str,
    leaderboard_path: Optional[Path] = None,
) -> Optional[SliceEdge]:
    """Look up a slice's edge metrics.

    Lookup order:
      1. localdata/monitored_edge_metrics.csv (curated, execution-owned; same
         schema columns as the candidate leaderboard) -- survives research
         reruns that overwrite candidate_leaderboard.csv.
      2. candidate_leaderboard.csv (or the explicit leaderboard_path).

    Returns None if no source has a matching
    (symbol, timeframe, slice_combination) row. Never raises.
    """
    if leaderboard_path is None and MONITORED_EDGE_METRICS_PATH.exists():
        curated = _load_edge_metrics_from(
            MONITORED_EDGE_METRICS_PATH, symbol, timeframe, slice_combination
        )
        if curated is not None:
            return curated
    path = Path(leaderboard_path) if leaderboard_path else Path(CANDIDATE_LEADERBOARD_PATH)
    return _load_edge_metrics_from(path, symbol, timeframe, slice_combination)


def _load_edge_metrics_from(
    path: Path,
    symbol: str,
    timeframe: str,
    slice_combination: str,
) -> Optional[SliceEdge]:
    """Read one edge-metrics CSV and return the matching SliceEdge, else None."""
    if not path.exists():
        return None
    try:
        lb = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return None
    if lb is None or lb.empty:
        return None

    sym_col = "symbol" if "symbol" in lb.columns else None
    tf_col = "timeframe" if "timeframe" in lb.columns else None
    slice_col = "slice_combination" if "slice_combination" in lb.columns else None
    if sym_col is None or tf_col is None or slice_col is None:
        return None

    mask = (
        (lb[sym_col].astype(str).str.upper() == symbol.upper())
        & (lb[tf_col].astype(str) == str(timeframe))
        & (lb[slice_col].astype(str) == str(slice_combination))
    )
    matches = lb[mask]
    if matches.empty:
        return None

    row = matches.iloc[0]

    def _get_float(col):
        if col not in matches.columns:
            return 0.0
        v = row.get(col)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return v if v == v else 0.0

    def _get_int(col):
        return int(round(_get_float(col)))

    def _get_bool(col):
        if col not in matches.columns:
            return False
        v = row.get(col)
        if isinstance(v, bool):
            return v
        if v in (1, "1", "True", "true", "TRUE"):
            return True
        try:
            return bool(float(v))
        except (TypeError, ValueError):
            return False

    return SliceEdge(
        mean_return=_get_float("valid_mean_ret_costadj"),
        excess_vs_parent=_get_float("valid_excess_vs_best_parent"),
        walk_forward_pass_count=_get_int("walk_forward_pass_count"),
        scenario_survived_count=_get_int("scenario_survived_count"),
        valid_n=_get_int("valid_n"),
        search_wide_bh_pass=_get_bool("search_wide_bh_pass"),
        search_wide_bonferroni_pass=_get_bool("search_wide_bonferroni_pass"),
        triage_bucket=str(row.get("triage_bucket", "") or ""),
    )


def compute_position_size(
    symbol: str,
    timeframe: str,
    slice_combination: str,
    close_adj: float,
    limits,
    *,
    atr: Optional[float] = None,
    equity: Optional[float] = None,
    leaderboard_path: Optional[Path] = None,
    conviction_sizing_enabled: Optional[bool] = None,
    cost_model: Optional[CostModel] = None,
) -> PositionSize:
    """Compute an edge- and volatility-aware position size.

    cost_model : realistic execution cost model. When provided (default is
        the conservative default_cost_model()), conviction nets the execution
        round-trip drag off the edge before sizing, so a cost-eating trade
        earns near-zero capital. Pass CostModel(commission_bps=0, spread_bps=0,
        slippage_bps=0) to disable cost adjustment (= pre-lever-4 behaviour).
    """
    # Resolve config with overrides.
    enabled = (
        conviction_sizing_enabled
        if conviction_sizing_enabled is not None
        else getattr(limits, "conviction_sizing_enabled", True)
    )
    eq = equity if equity is not None else getattr(limits, "account_equity_for_sizing", None)
    risk_fraction = getattr(limits, "risk_fraction_per_trade", 0.0) or 0.0
    max_notional = float(getattr(limits, "max_notional_per_position", 2500.0))
    if cost_model is None:
        cost_model = default_cost_model()

    # Bad price -> no trade.
    if close_adj is None or close_adj != close_adj or close_adj <= 0:
        return PositionSize(
            qty=0, conviction=0.0, sizing_mode="zero",
            target_notional=0.0, qty_notional=0, qty_risk=None, atr=None,
            conviction_result=None,
            expected_cost_bps_round_trip=cost_model.round_trip_bps(),
            reasons=["price invalid or non-positive"],
        )

    # Conviction (with graceful no-data fallback; cost nets only when edge known).
    if enabled:
        edge = load_edge_metrics(symbol, timeframe, slice_combination, leaderboard_path)
        cr = compute_conviction(edge, cost_model=cost_model)
    else:
        cr = ConvictionResult(
            conviction=1.0, mode="disabled",
            components={}, reasons=["conviction sizing disabled; equal-notional"],
        )
    conviction = cr.conviction

    # Stage A - conviction-weighted notional.
    target_notional = conviction * max_notional
    qty_notional = int(target_notional // close_adj)  # floor

    # Stage B - volatility normalization (risk rail).
    resolved_atr = None
    qty_risk = None
    if eq is not None and risk_fraction > 0.0:
        resolved_atr = _resolve_atr(symbol, timeframe, atr)
        if resolved_atr is not None and resolved_atr > 0:
            risk_dollars = conviction * risk_fraction * float(eq)
            qty_risk = int(risk_dollars // resolved_atr)

    if qty_risk is not None:
        qty = min(qty_notional, qty_risk)
        sizing_mode = "conviction_with_vol_rail"
        atr_for_result = resolved_atr
    else:
        qty = qty_notional
        sizing_mode = "conviction_notional_only"
        atr_for_result = None
        if cr.mode == "neutral_no_data":
            sizing_mode = "fallback_no_data"
        elif cr.mode == "cost_negated":
            sizing_mode = "cost_negated"

    qty = max(0, qty)

    reasons = list(cr.reasons)
    reasons.append(f"target_notional={target_notional:.2f} (conviction {conviction:.3f} * cap {max_notional:.2f})")
    reasons.append(f"qty_notional={qty_notional}")
    if qty_risk is not None:
        reasons.append(f"qty_risk={qty_risk} (vol rail; atr={resolved_atr:.4f})")
        reasons.append(f"qty=min(qty_notional, qty_risk)={qty}")
    else:
        reasons.append(f"qty=qty_notional={qty}")

    return PositionSize(
        qty=qty,
        conviction=conviction,
        sizing_mode=sizing_mode,
        target_notional=target_notional,
        qty_notional=qty_notional,
        qty_risk=qty_risk,
        atr=atr_for_result,
        conviction_result=cr,
        expected_cost_bps_round_trip=cost_model.round_trip_bps(),
        reasons=reasons,
    )

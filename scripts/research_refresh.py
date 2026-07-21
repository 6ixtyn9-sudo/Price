"""Autonomous research-refresh controller.

The controller is deliberately separated from live execution. It maintains a
full-universe research state, enforces the new-data gate before discovery,
runs isolated rolling-bin discovery/validation when eligible, produces regime
coverage and regime-stratified diagnostics, and writes a candidate lifecycle
registry. It never modifies monitored_slices.csv and never places orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from price.config import SYMBOLS  # noqa: E402
from price.warehouse import load_from_warehouse  # noqa: E402
import discover_slices  # noqa: E402
import validate_slices  # noqa: E402
from research_lifecycle import apply_registry_to_monitored, build_registry  # noqa: E402
from research_observations import build_regime_opportunity_rates  # noqa: E402
from research_regime_coverage import build_coverage  # noqa: E402


RESEARCH_DIR = Path("localdata/research")
STATE_PATH = RESEARCH_DIR / "refresh_state.json"
DISCOVERED_PATH = RESEARCH_DIR / "discovered_slices_rolling.csv"
VALIDATED_PATH = RESEARCH_DIR / "validated_slices_rolling.csv"
LEADERBOARD_PATH = RESEARCH_DIR / "candidate_leaderboard_rolling.csv"
REGIME_DIAGNOSTICS_PATH = RESEARCH_DIR / "regime_stratified_diagnostics_rolling.csv"


def _coverage(symbols, timeframes=("1d", "1h")) -> dict:
    """Build coverage dict for all symbols/timeframes in one pass."""
    out = {}
    for symbol in symbols:
        for tf in timeframes:
            key = f"{symbol}:{tf}"
            df = load_from_warehouse(symbol, tf)
            if df is None or df.empty:
                out[key] = {"count": 0, "last_bar": None}
                continue
            out[key] = {
                "count": int(len(df)),
                "last_bar": str(df["bar_ts_utc"].max()),
            }
    return out


def _daily_bar_deltas(previous: dict, current: dict) -> dict:
    """Compute per-symbol daily bar deltas from coverage dicts."""
    return {
        symbol: max(
            0,
            int(values.get("count", 0))
            - int(previous.get(symbol, {}).get("count", 0)),
        )
        for symbol, values in current.items()
    }


def _new_daily_bars(previous: dict, current: dict) -> int:
    """Aggregate delta for telemetry only; never use this for discovery gating."""
    return sum(_daily_bar_deltas(previous, current).values())


def _eligible_discovery_symbols(previous: dict, current: dict, min_new_daily_bars: int) -> list[str]:
    """Return symbols with enough genuinely fresh daily observations.

    The gate is per-symbol, not aggregate: 60 new bars across 236 symbols
    would otherwise be reached in a day and would not represent a quarter of
    fresh evidence for any individual candidate.
    """
    if not previous:
        return []
    deltas = _daily_bar_deltas(previous, current)
    return sorted(symbol for symbol, delta in deltas.items() if delta >= min_new_daily_bars)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(payload: dict) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def _monitored_diagnostic_bin_mode(monitored_path: Path) -> str:
    """Resolve the state-binning mode used by the active monitored book.

    Diagnostics must not silently evaluate an insample book with rolling bins
    (or vice versa). A single active mode is expected because one discovery
    cycle produces one vocabulary. Mixed legacy rows fall back to insample and
    are reported explicitly rather than pretending the diagnostic is uniform.
    """
    try:
        monitored = pd.read_csv(monitored_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError):
        return "insample"
    if monitored.empty or "bin_mode" not in monitored.columns:
        return "insample"
    modes = {
        str(value).strip().lower()
        for value in monitored["bin_mode"].dropna().tolist()
        if str(value).strip().lower() in {"insample", "rolling"}
    }
    if len(modes) == 1:
        return next(iter(modes))
    if len(modes) > 1:
        print(
            "Research diagnostics: mixed monitored bin_mode values detected; "
            "using insample until the book is rebuilt from one discovery run."
        )
    return "insample"


def _tag_diagnostic_output(path: Path, bin_mode: str) -> bool:
    """Persist the binning provenance beside diagnostic rows."""
    if not path.exists():
        return False
    try:
        frame = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError):
        return False
    frame["bin_mode"] = bin_mode
    frame.to_csv(path, index=False)
    return True


def run_refresh(
    symbols=None,
    timeframes=("1d", "1h", "15m"),
    min_new_daily_bars: int = 5, # weekly
    allow_discovery: bool = False,
    condition_symbols=("USO", "TLT"),
    enable_auto_promotion: bool = False,
    apply_monitored_slices: bool = False,
    allow_unsharded_discovery: bool = False,
    force_discovery: bool = False,  # NEW: force discovery on first run
) -> dict:
    symbols = list(symbols or SYMBOLS)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    # Build coverage for daily timeframe only (used for discovery gate).
    # The DURABLE baseline (discovery_baseline_coverage) persists across
    # refresh cycles and is only reset when discovery actually runs.  Without
    # this, the gate logic compared current coverage against yesterday's
    # snapshot (which was itself overwritten every refresh), so bars never
    # accumulated beyond 1 and the gate stayed permanently closed.
    # daily_coverage is still written for telemetry; the delta comparison
    # uses the durable baseline.
    state = _load_state()
    previous_telemetry = state.get("daily_coverage", {})
    discovery_baseline = state.get("discovery_baseline_coverage", None)
    current = _coverage(symbols, ("1d",))

    new_bars = _new_daily_bars(previous_telemetry, current)

    if force_discovery:
        eligible_symbols = symbols
    else:
        # Compare against the DURABLE baseline so bars accumulate across days.
        comparison_baseline = discovery_baseline if discovery_baseline is not None else previous_telemetry
        eligible_symbols = _eligible_discovery_symbols(
            comparison_baseline, current, min_new_daily_bars
        )

    # The first refresh establishes the coverage baseline. On later refreshes,
    # require fresh evidence for at least 80% of the active universe before
    # re-running the full grid; otherwise discovery remains skipped.
    required_symbols = max(1, int(len(symbols) * 0.50)) # weekly was 80%
    fresh_data_gate_open = bool(len(eligible_symbols) >= required_symbols)
    discovery_requested = bool(allow_discovery)
    sharded_discovery_required = bool(
        discovery_requested
        and fresh_data_gate_open
        and len(symbols) > 50
        and len(timeframes) > 1
        and not allow_unsharded_discovery
    )
    discovery_allowed = bool(
        discovery_requested
        and fresh_data_gate_open
        and not sharded_discovery_required
    )
    discovery_block_reason = None
    if sharded_discovery_required:
        discovery_block_reason = (
            "full-universe multi-timeframe discovery requires sharded workflow"
        )
    elif discovery_requested and not fresh_data_gate_open:
        discovery_block_reason = "fresh-data gate closed"

    # Always produce coverage and existing-paper opportunity telemetry.
    coverage = build_coverage(symbols, ("1d", "1h"))
    coverage.to_csv(RESEARCH_DIR / "universe_regime_coverage.csv", index=False)
    observations = build_regime_opportunity_rates()
    observations.to_csv(RESEARCH_DIR / "regime_opportunity_rates.csv", index=False)

    discovery_ran = False
    regime_tracks_ran = False
    regime_tracks_bin_mode = None

    # --- DAILY regime tracks for monitored slices (always, not gated) ---
    try:
        monitored_path = Path("localdata/monitored_slices.csv")
        if monitored_path.exists() and monitored_path.stat().st_size > 0:
            regime_tracks_bin_mode = _monitored_diagnostic_bin_mode(monitored_path)
            regime_output = RESEARCH_DIR / "regime_stratified_diagnostics_rolling.csv"
            date_output = RESEARCH_DIR / "date_range_diagnostics_rolling.csv"
            validate_slices.run_regime_stratified_diagnostics(
                slices_path=str(monitored_path),
                output_path=str(regime_output),
                diagnostic_scope="leaderboard-top",
                top_n=50,
                bin_mode=regime_tracks_bin_mode,
            )
            validate_slices.run_date_range_diagnostics(
                slices_path=str(monitored_path),
                output_path=str(date_output),
                diagnostic_scope="leaderboard-top",
                top_n=50,
                bin_mode=regime_tracks_bin_mode,
            )
            _tag_diagnostic_output(regime_output, regime_tracks_bin_mode)
            _tag_diagnostic_output(date_output, regime_tracks_bin_mode)
            regime_tracks_ran = regime_output.exists() and date_output.exists()
            try:
                import pandas as pd
                leaderboard_path = RESEARCH_DIR / "candidate_leaderboard_rolling.csv"
                if not leaderboard_path.exists():
                    leaderboard_path = Path("localdata/candidate_leaderboard.csv")
                if leaderboard_path.exists():
                    lb = pd.read_csv(leaderboard_path)
                    if not lb.empty and not observations.empty and "valid_mean_ret_costadj" in lb.columns:
                        lb_map = lb.set_index(["symbol", "timeframe", "slice_combination"])["valid_mean_ret_costadj"].to_dict()
                        rows = []
                        for _, r in observations.iterrows():
                            key = (r["symbol"], r["timeframe"], r["slice_combination"])
                            mr = lb_map.get(key)
                            if mr is None:
                                continue
                            blocked = int(r.get("risk_blocked_opportunities", 0) or 0)
                            if blocked>0:
                                rows.append({
                                    "symbol": r["symbol"], "timeframe": r["timeframe"],
                                    "slice_combination": r["slice_combination"], "regime": r["regime"],
                                    "matched_opportunities": int(r.get("matched_opportunities",0) or 0),
                                    "risk_blocked_opportunities": blocked,
                                    "risk_block_rate": r.get("risk_block_rate"),
                                    "valid_mean_ret_costadj": float(mr),
                                    "potential_missed_pnl": blocked*float(mr),
                                })
                        if rows:
                            pd.DataFrame(rows).sort_values("potential_missed_pnl", ascending=False).to_csv(RESEARCH_DIR / "opportunity_roi_insights.csv", index=False)
            except Exception as e:
                print(f"ROI insights failed: {e}")
    except Exception as e:
        print(f"Daily diagnostics failed: {e}")

    if discovery_allowed:
        if DISCOVERED_PATH.exists():
            DISCOVERED_PATH.unlink()
        discover_slices.DISCOVERED_SLICES_PATH = str(DISCOVERED_PATH)
        for timeframe in timeframes:
            discover_slices.run_discovery(
                target_symbols=eligible_symbols,
                timeframe=timeframe,
                min_samples=15,
                append=DISCOVERED_PATH.exists(),
                cond_symbols=list(condition_symbols),
                bin_mode="rolling",
            )
        if DISCOVERED_PATH.exists():
            validate_slices.VALIDATED_SLICES_PATH = str(VALIDATED_PATH)
            validate_slices.CANDIDATE_LEADERBOARD_PATH = str(LEADERBOARD_PATH)
            validate_slices.run_candidate_leaderboard(
                slices_path=str(DISCOVERED_PATH),
                output_path=str(LEADERBOARD_PATH),
                bin_mode="rolling",
            )
            if LEADERBOARD_PATH.exists():
                validate_slices.run_regime_stratified_diagnostics(
                    output_path=str(REGIME_DIAGNOSTICS_PATH),
                    diagnostic_scope="leaderboard-top",
                    top_n=25,
                    slices_path=str(DISCOVERED_PATH),
                    bin_mode="rolling",
                )
                registry = build_registry(
                    LEADERBOARD_PATH,
                    enable_auto_promotion=enable_auto_promotion,
                )
                if apply_monitored_slices:
                    if not enable_auto_promotion:
                        raise RuntimeError(
                            "apply_monitored_slices requires enable_auto_promotion"
                        )
                    apply_registry_to_monitored(registry)
                regime_tracks_ran = REGIME_DIAGNOSTICS_PATH.exists()
                regime_tracks_bin_mode = "rolling"
            discovery_ran = True

    state = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": len(symbols),
        "timeframes": list(timeframes),
        "daily_coverage": current,
        # Durable baseline: only reset when discovery runs so bars can
        # accumulate across days.  Falls back to the previous baseline
        # on the first refresh after deployment (when the key is absent).
        "discovery_baseline_coverage": (
            current if discovery_ran
            else discovery_baseline if discovery_baseline is not None
            else previous_telemetry  # first-run migration: seed from legacy daily_coverage
        ),
        "new_daily_bars_since_previous_refresh": new_bars,
        "eligible_discovery_symbols": eligible_symbols,
        "eligible_discovery_symbol_count": len(eligible_symbols),
        "required_discovery_symbol_count": required_symbols,
        "min_new_daily_bars": min_new_daily_bars,
        "discovery_requested": discovery_requested,
        "fresh_data_gate_open": fresh_data_gate_open,
        "sharded_discovery_required": sharded_discovery_required,
        "unsharded_discovery_allowed": discovery_allowed,
        "discovery_allowed": discovery_allowed,
        "discovery_block_reason": discovery_block_reason,
        "discovery_ran": discovery_ran,
        "regime_tracks_ran": regime_tracks_ran,
        "regime_tracks_bin_mode": regime_tracks_bin_mode,
        "orders_placed": False,
        "monitored_slices_modified": bool(apply_monitored_slices),
        "automatic_promotion_enabled": bool(enable_auto_promotion),
    }
    _write_state(state)
    print(json.dumps(state, indent=2))
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the isolated full-universe research refresh controller.")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--timeframes", nargs="+", default=["1d", "1h", "15m"])
    parser.add_argument("--min-new-daily-bars", type=int, default=60)
    parser.add_argument("--allow-discovery", action="store_true")
    parser.add_argument("--condition-on", nargs="+", default=["USO", "TLT"])
    parser.add_argument("--enable-auto-promotion", action="store_true")
    parser.add_argument("--apply-monitored-slices", action="store_true")
    parser.add_argument(
        "--allow-unsharded-discovery",
        action="store_true",
        help="Override the safety guard for a deliberately small/unsharded research run.",
    )
    parser.add_argument(
        "--force-discovery",
        action="store_true",
        help="Force discovery on first run (bypass fresh-data gate).",
    )
    args = parser.parse_args()
    run_refresh(
        symbols=args.symbols,
        timeframes=tuple(args.timeframes),
        min_new_daily_bars=args.min_new_daily_bars,
        allow_discovery=args.allow_discovery,
        condition_symbols=tuple(args.condition_on),
        enable_auto_promotion=args.enable_auto_promotion,
        apply_monitored_slices=args.apply_monitored_slices,
        allow_unsharded_discovery=args.allow_unsharded_discovery,
        force_discovery=args.force_discovery,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
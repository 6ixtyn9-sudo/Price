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


def run_refresh(
    symbols=None,
    timeframes=("1d", "1h", "15m"),
    min_new_daily_bars: int = 60,
    allow_discovery: bool = False,
    condition_symbols=("USO", "TLT"),
    enable_auto_promotion: bool = False,
    apply_monitored_slices: bool = False,
    allow_unsharded_discovery: bool = False,
    force_discovery: bool = False,  # NEW: force discovery on first run
) -> dict:
    symbols = list(symbols or SYMBOLS)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    # Build coverage for daily timeframe only (used for discovery gate)
    previous = _load_state().get("daily_coverage", {})
    current = _coverage(symbols, ("1d",))  # Only 1d for gate
    new_bars = _new_daily_bars(previous, current)

    if force_discovery:
        eligible_symbols = symbols
    else:
        eligible_symbols = _eligible_discovery_symbols(
            previous, current, min_new_daily_bars
        )

    # The first refresh establishes the coverage baseline. On later refreshes,
    # require fresh evidence for at least 80% of the active universe before
    # re-running the full grid; otherwise discovery remains skipped.
    required_symbols = max(1, int(len(symbols) * 0.80))
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
            discovery_ran = True

    state = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": len(symbols),
        "timeframes": list(timeframes),
        "daily_coverage": current,
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
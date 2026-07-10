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


def _coverage(symbols) -> dict:
    out = {}
    for symbol in symbols:
        df = load_from_warehouse(symbol, "1d")
        if df is None or df.empty:
            out[symbol] = {"count": 0, "last_bar": None}
            continue
        out[symbol] = {
            "count": int(len(df)),
            "last_bar": str(df["bar_ts_utc"].max()),
        }
    return out


def _new_daily_bars(previous: dict, current: dict) -> int:
    return sum(
        max(0, int(values.get("count", 0)) - int(previous.get(symbol, {}).get("count", 0)))
        for symbol, values in current.items()
    )


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
) -> dict:
    symbols = list(symbols or SYMBOLS)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    previous = _load_state().get("daily_coverage", {})
    current = _coverage(symbols)
    new_bars = _new_daily_bars(previous, current)
    # The first refresh establishes the coverage baseline; it must not use
    # the entire historical warehouse as "new" evidence. Discovery becomes
    # eligible only on a later refresh after the required fresh-bar delta.
    discovery_allowed = bool(allow_discovery and previous and new_bars >= min_new_daily_bars)

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
                target_symbols=symbols,
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
        "min_new_daily_bars": min_new_daily_bars,
        "discovery_requested": bool(allow_discovery),
        "discovery_allowed": discovery_allowed,
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
    args = parser.parse_args()
    run_refresh(
        symbols=args.symbols,
        timeframes=tuple(args.timeframes),
        min_new_daily_bars=args.min_new_daily_bars,
        allow_discovery=args.allow_discovery,
        condition_symbols=tuple(args.condition_on),
        enable_auto_promotion=args.enable_auto_promotion,
        apply_monitored_slices=args.apply_monitored_slices,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

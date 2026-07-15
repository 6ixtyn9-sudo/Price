"""Isolated crypto-only research lane.

Purpose
-------
Give crypto its own search/validation family without touching the live equity
paper book. This script is intentionally additive and isolated:

- targets only crypto symbols
- defaults conditioning to BTC/USD and ETH/USD
- writes only under localdata/research/crypto/
- never modifies monitored_slices.csv
- never places orders

Why it exists
-------------
Crypto is already inside the warehouse/discovery substrate, but in the mixed
236-symbol family it is still being judged through an equity-heavy lens:
search-wide correction pooled with equities, conditioning on USO/TLT, and no
clean way to inspect crypto on its own. This script isolates crypto so we can
answer the research question honestly before touching the live book.

Red-team safety design
----------------------
- No shared live artifacts are written.
- validate_slices/discover_slices globals are temporarily redirected and then
  restored, so running this cannot clobber the current system's default
  localdata/discovered_slices.csv / candidate_leaderboard.csv.
- BTC/USD and ETH/USD are used as conditioning symbols for alts, but BTC and
  ETH themselves are still researched via self-exclusion batches (BTC
  conditioned on ETH only, ETH conditioned on BTC only) rather than being
  accidentally skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import discover_slices  # noqa: E402
import validate_slices  # noqa: E402
from price.config import ALLOWLIST_CACHE_PATH, CRYPTO_SYMBOLS  # noqa: E402
from price.market_profiles import get_market_profile  # noqa: E402
from research_lifecycle import build_registry  # noqa: E402


_CRYPTO_PROFILE = get_market_profile("crypto")
DEFAULT_OUTPUT_DIR = Path(_CRYPTO_PROFILE.default_output_dir)
DEFAULT_BIN_MODE = _CRYPTO_PROFILE.default_bin_mode
DEFAULT_TIMEFRAMES = _CRYPTO_PROFILE.default_timeframes
DEFAULT_CONDITION_SYMBOLS = _CRYPTO_PROFILE.default_condition_symbols


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for symbol in symbols:
        s = str(symbol).strip().upper()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def load_crypto_symbols(path: Path | None = None) -> list[str]:
    """Load the active crypto research universe.

    Preference order:
      1. localdata/explicit_allowlist.json -> .crypto
      2. localdata/explicit_allowlist.json -> filter .all for slash pairs
      3. static CRYPTO_SYMBOLS fallback
    """
    allowlist_path = Path(path) if path else Path(ALLOWLIST_CACHE_PATH)
    if allowlist_path.exists():
        try:
            payload = json.loads(allowlist_path.read_text())
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            crypto = _normalize_symbols(payload.get("crypto", []))
            if crypto:
                return crypto
            all_symbols = _normalize_symbols(payload.get("all", []))
            filtered = [s for s in all_symbols if "/" in s]
            if filtered:
                return filtered
    return _normalize_symbols(CRYPTO_SYMBOLS)


def build_discovery_batches(
    target_symbols: Iterable[str],
    condition_symbols: Iterable[str],
) -> list[dict]:
    """Build safe discovery batches that avoid self-conditioning skips.

    discover_slices.run_discovery uses one shared cond_symbols list per run and
    skips a symbol completely if it appears in that conditioning list. For
    crypto we want alts conditioned on BTC+ETH, while still allowing BTC and
    ETH themselves to be researched. So we split the universe into:

      - non-conditioning symbols: conditioned on all requested symbols
      - each conditioning symbol: conditioned on the OTHER conditioning symbols

    Example:
      targets = [BTC/USD, ETH/USD, SOL/USD], cond = [BTC/USD, ETH/USD]
      -> [SOL/USD] with [BTC/USD, ETH/USD]
      -> [BTC/USD] with [ETH/USD]
      -> [ETH/USD] with [BTC/USD]
    """
    targets = _normalize_symbols(target_symbols)
    conds = _normalize_symbols(condition_symbols)
    cond_set = set(conds)
    batches: list[dict] = []

    non_cond = [symbol for symbol in targets if symbol not in cond_set]
    if non_cond:
        batches.append({
            "label": "alts",
            "symbols": non_cond,
            "condition_symbols": conds,
        })

    for cond_symbol in conds:
        if cond_symbol not in targets:
            continue
        other_conds = [symbol for symbol in conds if symbol != cond_symbol]
        batches.append({
            "label": cond_symbol.replace("/", "-"),
            "symbols": [cond_symbol],
            "condition_symbols": other_conds,
        })

    return batches


@contextmanager
def isolated_research_paths(output_dir: Path):
    """Temporarily redirect discovery/validation globals into output_dir."""
    discovered_path = output_dir / "discovered_slices_crypto_rolling.csv"
    validated_path = output_dir / "validated_slices_crypto_rolling.csv"
    leaderboard_path = output_dir / "candidate_leaderboard_crypto_rolling.csv"
    scenario_path = output_dir / "validation_scenario_grid_crypto_rolling.csv"
    walk_path = output_dir / "walk_forward_diagnostics_crypto_rolling.csv"
    date_path = output_dir / "date_range_diagnostics_crypto_rolling.csv"
    regime_path = output_dir / "regime_stratified_diagnostics_crypto_rolling.csv"

    state = {
        "discover": discover_slices.DISCOVERED_SLICES_PATH,
        "discovered": validate_slices.DISCOVERED_SLICES_PATH,
        "validated": validate_slices.VALIDATED_SLICES_PATH,
        "leaderboard": validate_slices.CANDIDATE_LEADERBOARD_PATH,
        "scenario": validate_slices.SCENARIO_GRID_PATH,
        "walk": validate_slices.WALK_FORWARD_DIAGNOSTICS_PATH,
        "date": validate_slices.DATE_RANGE_DIAGNOSTICS_PATH,
        "regime": validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH,
    }

    discover_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.VALIDATED_SLICES_PATH = str(validated_path)
    validate_slices.CANDIDATE_LEADERBOARD_PATH = str(leaderboard_path)
    validate_slices.SCENARIO_GRID_PATH = str(scenario_path)
    validate_slices.WALK_FORWARD_DIAGNOSTICS_PATH = str(walk_path)
    validate_slices.DATE_RANGE_DIAGNOSTICS_PATH = str(date_path)
    validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH = str(regime_path)
    try:
        yield {
            "discovered": discovered_path,
            "validated": validated_path,
            "leaderboard": leaderboard_path,
            "scenario": scenario_path,
            "walk": walk_path,
            "date": date_path,
            "regime": regime_path,
        }
    finally:
        discover_slices.DISCOVERED_SLICES_PATH = state["discover"]
        validate_slices.DISCOVERED_SLICES_PATH = state["discovered"]
        validate_slices.VALIDATED_SLICES_PATH = state["validated"]
        validate_slices.CANDIDATE_LEADERBOARD_PATH = state["leaderboard"]
        validate_slices.SCENARIO_GRID_PATH = state["scenario"]
        validate_slices.WALK_FORWARD_DIAGNOSTICS_PATH = state["walk"]
        validate_slices.DATE_RANGE_DIAGNOSTICS_PATH = state["date"]
        validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH = state["regime"]


def _top_rows(frame: pd.DataFrame, columns: list[str], n: int = 15) -> list[dict]:
    if frame is None or frame.empty:
        return []
    keep = [col for col in columns if col in frame.columns]
    out = frame[keep].head(n).copy()
    return out.to_dict("records")


def build_summary(
    symbols: list[str],
    condition_symbols: list[str],
    discovered: pd.DataFrame,
    leaderboard: pd.DataFrame,
    registry: pd.DataFrame,
    output_dir: Path,
) -> dict:
    strict_gate_pass = int(registry["strict_gate_pass"].sum()) if not registry.empty and "strict_gate_pass" in registry.columns else 0
    status_counts = registry["status"].value_counts().to_dict() if not registry.empty and "status" in registry.columns else {}
    timeframe_counts = (
        leaderboard.groupby("timeframe").size().to_dict()
        if not leaderboard.empty and "timeframe" in leaderboard.columns
        else {}
    )
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bin_mode": DEFAULT_BIN_MODE,
        "symbols": symbols,
        "symbol_count": len(symbols),
        "condition_symbols": condition_symbols,
        "discovered_rows": int(len(discovered)),
        "leaderboard_rows": int(len(leaderboard)),
        "registry_rows": int(len(registry)),
        "strict_gate_pass_count": strict_gate_pass,
        "status_counts": status_counts,
        "timeframe_counts": timeframe_counts,
        "paper_proposal_count": int(status_counts.get("paper_proposal", 0)),
        "auto_approved_count": int(status_counts.get("auto_approved", 0)),
        "output_dir": str(output_dir),
        "top_candidates": _top_rows(
            leaderboard,
            [
                "rank", "symbol", "timeframe", "slice_combination", "side",
                "triage_bucket", "valid_n", "valid_mean_ret_costadj",
                "valid_p_value_nw", "walk_forward_pass_pattern",
                "search_wide_bh_pass", "search_wide_bonferroni_pass",
            ],
        ),
    }
    return summary


def run_crypto_research(
    symbols: list[str] | None = None,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    condition_symbols: tuple[str, ...] = DEFAULT_CONDITION_SYMBOLS,
    min_samples: int = 15,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    top_n_diagnostics: int = 25,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    target_symbols = _normalize_symbols(symbols or load_crypto_symbols())
    if not target_symbols:
        raise ValueError("No crypto symbols resolved for research run.")
    conds = _normalize_symbols(condition_symbols)

    batches = build_discovery_batches(target_symbols, conds)
    if not batches:
        raise ValueError("No crypto discovery batches could be built.")

    with isolated_research_paths(output_dir) as paths:
        for path in paths.values():
            if path.exists():
                path.unlink()

        for timeframe in timeframes:
            for batch in batches:
                discover_slices.run_discovery(
                    target_symbols=batch["symbols"],
                    timeframe=timeframe,
                    min_samples=min_samples,
                    append=paths["discovered"].exists(),
                    cond_symbols=batch["condition_symbols"] or None,
                    bin_mode=DEFAULT_BIN_MODE,
                    profile="crypto",
                )

        if not paths["discovered"].exists():
            discovered = pd.DataFrame()
            leaderboard = pd.DataFrame()
            registry = pd.DataFrame()
        else:
            try:
                discovered = pd.read_csv(paths["discovered"])
            except pd.errors.EmptyDataError:
                discovered = pd.DataFrame()

            if discovered.empty:
                leaderboard = pd.DataFrame()
                registry = pd.DataFrame()
                pd.DataFrame().to_csv(paths["validated"], index=False)
                pd.DataFrame().to_csv(paths["leaderboard"], index=False)
            else:
                leaderboard = validate_slices.run_candidate_leaderboard(
                    slices_path=str(paths["discovered"]),
                    output_path=str(paths["leaderboard"]),
                    bin_mode=DEFAULT_BIN_MODE,
                )
                registry = build_registry(
                    paths["leaderboard"],
                    output_path=output_dir / "candidate_registry_crypto_rolling.csv",
                    enable_auto_promotion=False,
                )

                if not leaderboard.empty:
                    validate_slices.run_date_range_diagnostics(
                        slices_path=str(paths["discovered"]),
                        output_path=str(paths["date"]),
                        diagnostic_scope="leaderboard-top",
                        top_n=top_n_diagnostics,
                        bin_mode=DEFAULT_BIN_MODE,
                    )
                    validate_slices.run_regime_stratified_diagnostics(
                        slices_path=str(paths["discovered"]),
                        output_path=str(paths["regime"]),
                        diagnostic_scope="leaderboard-top",
                        top_n=top_n_diagnostics,
                        bin_mode=DEFAULT_BIN_MODE,
                    )

        summary = build_summary(target_symbols, conds, discovered, leaderboard, registry, output_dir)
        summary_path = output_dir / "crypto_research_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated crypto-only research.")
    parser.add_argument("--symbols", nargs="+", default=None, help="Explicit crypto symbols to research.")
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        choices=["1d", "1h", "15m"],
        help="Timeframes to include in the crypto-only lane.",
    )
    parser.add_argument(
        "--condition-on",
        nargs="+",
        default=list(DEFAULT_CONDITION_SYMBOLS),
        help="Conditioning symbols for crypto discovery (default: BTC/USD ETH/USD).",
    )
    parser.add_argument("--min-samples", type=int, default=15)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-n-diagnostics", type=int, default=25)
    args = parser.parse_args()

    run_crypto_research(
        symbols=args.symbols,
        timeframes=tuple(args.timeframes),
        condition_symbols=tuple(args.condition_on),
        min_samples=args.min_samples,
        output_dir=args.output_dir,
        top_n_diagnostics=args.top_n_diagnostics,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

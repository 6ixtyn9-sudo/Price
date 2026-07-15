"""Isolated futures-only research lane.

Scope
-----
Research foundation only. This lane uses canonical FUT/* symbols, writes only
under localdata/research/futures/, and never touches the live equity paper
book. It is intentionally conservative: daily-first by default, no execution,
no monitored-book sync.
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
from price.futures_metadata import get_research_futures_symbols  # noqa: E402
from price.market_profiles import get_market_profile  # noqa: E402
from research_lifecycle import build_registry  # noqa: E402


_FUTURES_PROFILE = get_market_profile("futures")
DEFAULT_OUTPUT_DIR = Path(_FUTURES_PROFILE.default_output_dir)
DEFAULT_BIN_MODE = _FUTURES_PROFILE.default_bin_mode
DEFAULT_TIMEFRAMES = _FUTURES_PROFILE.default_timeframes


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for symbol in symbols:
        s = str(symbol).strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


@contextmanager
def isolated_research_paths(output_dir: Path):
    discovered_path = output_dir / "discovered_slices_futures_rolling.csv"
    validated_path = output_dir / "validated_slices_futures_rolling.csv"
    leaderboard_path = output_dir / "candidate_leaderboard_futures_rolling.csv"
    date_path = output_dir / "date_range_diagnostics_futures_rolling.csv"
    regime_path = output_dir / "regime_stratified_diagnostics_futures_rolling.csv"

    state = {
        "discover": discover_slices.DISCOVERED_SLICES_PATH,
        "discovered": validate_slices.DISCOVERED_SLICES_PATH,
        "validated": validate_slices.VALIDATED_SLICES_PATH,
        "leaderboard": validate_slices.CANDIDATE_LEADERBOARD_PATH,
        "date": validate_slices.DATE_RANGE_DIAGNOSTICS_PATH,
        "regime": validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH,
    }

    discover_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.VALIDATED_SLICES_PATH = str(validated_path)
    validate_slices.CANDIDATE_LEADERBOARD_PATH = str(leaderboard_path)
    validate_slices.DATE_RANGE_DIAGNOSTICS_PATH = str(date_path)
    validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH = str(regime_path)
    try:
        yield {
            "discovered": discovered_path,
            "validated": validated_path,
            "leaderboard": leaderboard_path,
            "date": date_path,
            "regime": regime_path,
        }
    finally:
        discover_slices.DISCOVERED_SLICES_PATH = state["discover"]
        validate_slices.DISCOVERED_SLICES_PATH = state["discovered"]
        validate_slices.VALIDATED_SLICES_PATH = state["validated"]
        validate_slices.CANDIDATE_LEADERBOARD_PATH = state["leaderboard"]
        validate_slices.DATE_RANGE_DIAGNOSTICS_PATH = state["date"]
        validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH = state["regime"]


def _top_rows(frame: pd.DataFrame, columns: list[str], n: int = 15) -> list[dict]:
    if frame is None or frame.empty:
        return []
    keep = [col for col in columns if col in frame.columns]
    return frame[keep].head(n).to_dict("records")


def run_futures_research(
    symbols: list[str] | None = None,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    condition_symbols: tuple[str, ...] = (),
    min_samples: int = 15,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    top_n_diagnostics: int = 15,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    target_symbols = _normalize_symbols(symbols or get_research_futures_symbols())
    if not target_symbols:
        raise ValueError("No futures symbols resolved for research run.")
    conds = _normalize_symbols(condition_symbols)

    with isolated_research_paths(output_dir) as paths:
        for path in paths.values():
            if path.exists():
                path.unlink()

        for timeframe in timeframes:
            discover_slices.run_discovery(
                target_symbols=target_symbols,
                timeframe=timeframe,
                min_samples=min_samples,
                append=paths["discovered"].exists(),
                cond_symbols=conds or None,
                bin_mode=DEFAULT_BIN_MODE,
                profile="futures",
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
                    output_path=output_dir / "candidate_registry_futures_rolling.csv",
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

        status_counts = registry["status"].value_counts().to_dict() if not registry.empty and "status" in registry.columns else {}
        summary = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "bin_mode": DEFAULT_BIN_MODE,
            "symbols": target_symbols,
            "symbol_count": len(target_symbols),
            "condition_symbols": conds,
            "discovered_rows": int(len(discovered)),
            "leaderboard_rows": int(len(leaderboard)),
            "registry_rows": int(len(registry)),
            "strict_gate_pass_count": int(registry["strict_gate_pass"].sum()) if not registry.empty and "strict_gate_pass" in registry.columns else 0,
            "status_counts": status_counts,
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
        (output_dir / "futures_research_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated futures-only research.")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        choices=["1d", "1h", "15m"],
    )
    parser.add_argument("--condition-on", nargs="+", default=[])
    parser.add_argument("--min-samples", type=int, default=15)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-n-diagnostics", type=int, default=15)
    args = parser.parse_args()

    run_futures_research(
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

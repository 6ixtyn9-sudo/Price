"""ML slice discovery -> validation bridge (Phase V5).

Runs LightGBM-based discovery, scores the extracted feature interactions,
converts the promising ones into the binned state_*=value slice format the
existing validate_slices.py pipeline consumes, and writes
localdata/ml_candidate_slices.csv in the discovered_slices.csv schema.

This closes the loop recorded as aspirational in HANDOVER.md V5: ML-discovered
feature interactions now flow through the same V4 validation discipline
(train/valid + cost + Newey-West + walk-forward + parent-excess) as
combinatorial discovery, without any new validation code.

Usage:
    python3 scripts/ml_to_slices.py --symbol SPY --timeframe 1d
    python3 scripts/ml_to_slices.py --symbols SPY QQQ --timeframe 1d --append

Then validate manually:
    python3 scripts/validate_slices.py \\
        --slices-path localdata/ml_candidate_slices.csv --candidate-leaderboard
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


import argparse
from pathlib import Path

import pandas as pd

from price.config import SYMBOLS
from price.ml_discovery import (
    evaluate_interactions,
    interactions_to_state_slices,
    prepare_ml_frame,
    run_ml_discovery,
)

OUTPUT_PATH = "localdata/ml_candidate_slices.csv"

# Default "promising" thresholds match the V5 handover's candidate filter.
DEFAULT_N_SAMPLES_MIN = 30
DEFAULT_MEAN_RETURN_MIN = 0.0008
DEFAULT_SHARPE_MIN = 0.20


def run(
    target_symbols,
    timeframe,
    target_type,
    append,
    include_interactions,
    max_interaction_size,
    n_samples_min,
    mean_return_min,
    sharpe_min,
    eval_min_samples,
    out_path,
    bin_mode="insample",
):
    symbols = [s.upper() for s in (target_symbols or SYMBOLS)]
    all_candidates = []

    for symbol in symbols:
        print(f"\n=== ML discovery: {symbol} ({timeframe}, target={target_type}, bin_mode={bin_mode}) ===")

        result = run_ml_discovery(
            symbol,
            timeframe,
            target_type=target_type,
            include_interactions=include_interactions,
            max_interaction_size=max_interaction_size,
        )
        if result.empty:
            print(f"  -> No ML discovery results for {symbol} {timeframe}.")
            continue

        interactions = result[result["interaction_size"] > 1].to_dict("records")
        if not interactions:
            print("  -> No multi-feature interactions extracted from the model.")
            continue

        df = prepare_ml_frame(symbol, timeframe, target_type=target_type)
        if df.empty:
            print(f"  -> Empty ML frame for {symbol} {timeframe}; cannot score.")
            continue

        scored = evaluate_interactions(
            df, interactions, min_samples=eval_min_samples, bin_mode=bin_mode,
        )
        if scored.empty:
            print("  -> No interactions passed the scoring sample floor.")
            continue

        mask = (
            (scored["n_samples"] >= n_samples_min)
            & (scored["mean_return"] > mean_return_min)
            & (scored["sharpe_proxy"] > sharpe_min)
        )
        promising = scored[mask].copy()
        if promising.empty:
            print(
                f"  -> No interactions met the promising thresholds "
                f"(n>={n_samples_min}, mean>{mean_return_min}, "
                f"sharpe>{sharpe_min})."
            )
            continue

        candidates = interactions_to_state_slices(
            df, promising, symbol, timeframe, bin_mode=bin_mode,
        )
        if candidates.empty:
            print("  -> Promising interactions found, but none mapped to state slices.")
            continue

        print(f"  -> {len(candidates)} candidate state-slices from {len(promising)} promising interactions:")
        print(candidates[["slice_combination", "ml_slice_key"]].to_string(index=False))
        all_candidates.append(candidates)

    if not all_candidates:
        print("\nNo ML candidate slices produced.")
        return

    final = pd.concat(all_candidates, ignore_index=True)

    output_path = Path(out_path)
    if append and output_path.exists():
        existing = pd.read_csv(output_path)
        # Replace prior ML rows for the symbol/timeframe pairs covered by this
        # run, then append the fresh results (mirrors discover_slices.py).
        covered = {(s, timeframe) for s in symbols}
        keep_mask = existing.apply(
            lambda r: (r.get("symbol"), r.get("timeframe")) not in covered
            or r.get("source") != "ml_interaction",
            axis=1,
        )
        existing_keep = existing[keep_mask]
        final = pd.concat([existing_keep, final], ignore_index=True)

    final.to_csv(output_path, index=False)
    action = "Appended ML candidate slices to" if append else "Saved ML candidate slices to"
    print(f"\n{action} {output_path}")
    print("\nNext step - run them through V4 validation:")
    print(
        f"  python3 scripts/validate_slices.py "
        f"--slices-path {output_path} --candidate-leaderboard --bin-mode {bin_mode}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert ML feature interactions into validatable state slices."
    )
    parser.add_argument("--symbol", help="Single symbol to run ML discovery on")
    parser.add_argument(
        "--symbols", nargs="+", help="Multiple symbols (mutually exclusive with --symbol)"
    )
    parser.add_argument(
        "--timeframe", default="1d", choices=["15m", "1h", "1d"], help="Timeframe to explore"
    )
    parser.add_argument(
        "--target-type",
        default="regression",
        choices=["regression", "classification"],
        help="LightGBM target type",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge into existing ml_candidate_slices.csv (replaces ML rows for the "
        "same symbol/timeframe pairs covered by this run) instead of overwriting.",
    )
    parser.add_argument(
        "--no-interactions", action="store_true", help="Only emit single-feature candidates"
    )
    parser.add_argument(
        "--max-interaction-size",
        type=int,
        default=3,
        choices=[2, 3],
        help="Largest feature interaction size to extract",
    )
    parser.add_argument(
        "--n-samples-min", type=int, default=DEFAULT_N_SAMPLES_MIN, help="Promising n_samples floor"
    )
    parser.add_argument(
        "--mean-return-min",
        type=float,
        default=DEFAULT_MEAN_RETURN_MIN,
        help="Promising mean forward-return floor",
    )
    parser.add_argument(
        "--sharpe-min", type=float, default=DEFAULT_SHARPE_MIN, help="Promising sharpe-proxy floor"
    )
    parser.add_argument(
        "--eval-min-samples",
        type=int,
        default=15,
        help="Minimum samples for evaluate_interactions to keep an interaction at all",
    )
    parser.add_argument(
        "--bin-mode",
        default="insample",
        choices=["insample", "rolling"],
        help="How to bin quantile state fields and define the ML promising region. "
        "'insample' (default) = full-history quantiles (look-ahead-prone, original "
        "behaviour). 'rolling' = look-ahead-free expanding-window quantiles (the "
        "overfit-kill; bar T's boundary uses only bars before T). Use 'rolling' and "
        "then validate with --bin-mode rolling for a consistent end-to-end run.",
    )
    parser.add_argument("--output", default=OUTPUT_PATH, help="Output CSV path")
    args = parser.parse_args()

    if args.symbol and args.symbols:
        parser.error("Use either --symbol or --symbols, not both.")

    target_symbols = [args.symbol] if args.symbol else args.symbols

    run(
        target_symbols=target_symbols,
        timeframe=args.timeframe,
        target_type=args.target_type,
        append=args.append,
        include_interactions=not args.no_interactions,
        max_interaction_size=args.max_interaction_size,
        n_samples_min=args.n_samples_min,
        mean_return_min=args.mean_return_min,
        sharpe_min=args.sharpe_min,
        eval_min_samples=args.eval_min_samples,
        out_path=args.output,
        bin_mode=args.bin_mode,
    )
